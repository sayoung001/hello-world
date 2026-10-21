"""
일일 배치 (Stage 1~6 + 게시) — US 마감 +1시간 실행.

흐름:
  1. 데이터 로드(일봉)           ← downloader (FDR/yfinance)
  2. 규칙 기반 전체 스캔(무토큰)  ← screener (+ 매크로 레짐, 섹터 게이트)
  3. 추천 상위 N LLM 분석        ← agents.pipeline (뉴스/수급/밸류/종합)
  4. Notion 게시(아카이브)       ← notion_publisher
  5. Telegram 다이제스트(푸시)   ← notify.digest
     추천·섹터·매크로·매도신호를 한 건으로 요약해 보낸다. Notion은 '가서 보는'
     매체라 미국장 배치(KST 06~07시)를 먼저 알려주지 않으면 그날 장을 놓친다.
  (실시간 폭주는 별도 모니터 → Telegram)

데이터 로드는 ohlcv_map을 직접 주입하거나(테스트), symbols+기간으로 자동 다운로드.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd

from stock_auto.config.settings import Market, get_config
from stock_auto.data.downloader import load_or_download
from stock_auto.strategy.screener import screen_universe, ScreenResult
from stock_auto.agents.pipeline import run_agents, AgentPipelineResult


@dataclass
class BatchResult:
    market: Market
    date: str
    screen: ScreenResult
    agents: AgentPipelineResult = field(default_factory=AgentPipelineResult)
    sector_lines: list[str] = field(default_factory=list)   # 섹터 현황 요약(게시용)


def _load_map(symbols: list[str], market: Market, start: str,
              end: Optional[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for s in symbols:
        try:
            out[s] = load_or_download(s, start, end, market)
        except Exception as e:  # noqa: BLE001
            print(f"[batch] {s} 로드 실패: {type(e).__name__}: {e}")
    return out


def run_daily(
    market: Market,
    universe: Optional[list[str]] = None,
    ohlcv_map: Optional[dict[str, pd.DataFrame]] = None,
    index_ohlcv: Optional[dict[str, pd.DataFrame]] = None,
    stock_sector_etf: Optional[dict[str, str]] = None,
    sector_status: Optional[dict[str, int]] = None,
    sector_label_map: Optional[dict[str, str]] = None,
    sector_lines: Optional[list[str]] = None,
    earnings_blocked: Optional[dict[str, bool]] = None,
    earnings_days: Optional[dict[str, Any]] = None,
    llm_client: Any = None,
    telegram: Any = None,
    send_telegram: bool = True,
    notion: Any = None,
    notion_db_id: Optional[str] = None,
    notion_parent_page: Optional[str] = None,
    lookback_days: int = 400,
    top_n: int = 15,
    run_llm: bool = True,
) -> BatchResult:
    """
    universe/ohlcv_map 중 하나는 제공. index_ohlcv 없으면 지수 자동 다운로드.
    notion 제공 시 게시. llm_client=None이면 기본 Anthropic 생성(run_llm=True일 때).
    """
    # 날짜/기간은 '시장 거래소 현지시각' 기준 (서버 TZ 의존 제거 — 시간대 버그 수정)
    from stock_auto.config.clock import market_now, market_today
    today = market_today(market)
    start = (market_now(market) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    cfg = get_config(market)

    # 1) 데이터
    if ohlcv_map is None:
        if not universe:
            raise ValueError("universe 또는 ohlcv_map 필요")
        ohlcv_map = _load_map(universe, market, start, None)
    if index_ohlcv is None:
        index_ohlcv = _load_map(list(cfg.index_symbols), market, start, None)

    # 2) 규칙 기반 스캔 (무토큰)
    screen = screen_universe(
        ohlcv_map, market, index_ohlcv=index_ohlcv, top_n=top_n,
        stock_sector_etf=stock_sector_etf, sector_status=sector_status,
        earnings_blocked=earnings_blocked, earnings_days=earnings_days)
    n_gated = 0 if screen.gated is None else len(screen.gated)
    print(f"[batch] {market.value} 매크로 L{screen.macro.level}({screen.macro.label}) "
          f"[{screen.macro.detail}] | 후보 {len(screen.candidates)} "
          f"| 게이트 차단 {n_gated} | 실패 {len(screen.failures)}")

    result = BatchResult(market=market, date=today, screen=screen,
                         sector_lines=list(sector_lines or []))

    # 3) 추천 상위 N LLM 분석
    # run_agents 는 종목 단위로 실패를 격리하지만, 뉴스 수집이나 클라이언트 생성
    # 처럼 루프 바깥에서 터지는 것도 있다. 그 경우에도 규칙 시그널 기록·섹터
    # 요약·매도 신호는 살려야 한다 — 기록 누락은 소급 복구가 안 된다.
    if run_llm and not screen.candidates.empty:
        try:
            result.agents = run_agents(
                screen.candidates, market, client=llm_client,
                sector_label_map=sector_label_map, top_n=top_n)
            n_ok = len(result.agents.recommendations)
            n_bad = len(result.agents.failures)
            print(f"[batch] 추천 {n_ok}건 생성" + (f" · LLM 실패 {n_bad}건" if n_bad else ""))
            if n_bad and n_ok == 0:
                # 후보는 있는데 전부 실패 — 일시 오류가 아니라 코드 문제일 확률이 높다
                print("[batch] 🚨 LLM 분석이 전부 실패했습니다. 같은 오류가 반복되면 "
                      "스키마·모델 설정 문제입니다 (schema_check 실행).")
        except Exception as e:  # noqa: BLE001 — LLM 실패가 배치를 막지 않게
            print(f"[batch] ⚠️ LLM 분석 전체 실패 — 규칙 시그널만 기록합니다 "
                  f"({type(e).__name__}: {e})")

    # 3.5) 결과 라벨링 루프: 추천 전 건을 기록 (실행 여부 무관 — 선택 편향 방지)
    try:
        n = _record_signals(result)
        print(f"[batch] 신호 기록 {n}건 적립 (라벨링 대기)")
    except Exception as e:  # noqa: BLE001 — 기록 실패가 배치를 막지 않게
        print(f"[batch] 신호 기록 실패: {type(e).__name__}: {e}")

    # 3.6) 토큰 사용량·비용 기록 — 추정이 아니라 실측으로 남긴다
    if result.agents.usage:
        try:
            from stock_auto.agents.base import DEFAULT_MODEL
            from stock_auto.tracking import usage_log
            p = usage_log.record(
                market=market.value, date=screen.as_of or today,
                model=DEFAULT_MODEL, usage=result.agents.usage,
                n_reco=len(result.agents.recommendations),
                n_fail=len(result.agents.failures))
            cost = usage_log.estimate_cost(DEFAULT_MODEL, result.agents.usage)
            print(f"[batch] 사용량 기록 → {p}" +
                  (f" (${cost:.4f})" if cost is not None else " (단가 미등록)"))
        except Exception as e:  # noqa: BLE001
            print(f"[batch] 사용량 기록 실패: {type(e).__name__}: {e}")

    # 4) Notion 게시 (아카이브 — 기록·검색용)
    if notion is not None and getattr(notion, "enabled", False):
        _publish_notion(notion, notion_db_id, notion_parent_page,
                        result, sector_label_map or {})

    # 5) Telegram 다이제스트 (푸시 — 오늘 뭘 볼지)
    #    Notion만 쓰면 (a) 미설정 시 결과가 콘솔에만 남고 (b) '가서 봐야' 한다.
    #    미국장 배치는 KST 06~07시에 끝나므로 먼저 알려주지 않으면 그날 장을 놓친다.
    if send_telegram:
        try:
            _send_digest(result, telegram)
        except Exception as e:  # noqa: BLE001 — 알림 실패가 배치를 망치지 않게
            print(f"[batch] 텔레그램 다이제스트 실패: {type(e).__name__}: {e}")
        # 5.5) 추천 종목 차트 — 다이제스트 뒤에 추천당 1장 (코인봇 sendPhoto 방식)
        try:
            _send_charts(result, ohlcv_map, telegram)
        except Exception as e:  # noqa: BLE001 — 차트 실패가 배치를 망치지 않게
            print(f"[batch] 차트 전송 실패: {type(e).__name__}: {e}")
    return result


def _send_charts(result: BatchResult, ohlcv_map: dict, telegram: Any = None) -> int:
    """
    LLM 추천(recommendations) 각각에 대해 일봉 차트 PNG를 보낸다.
    매도신호(EXIT)까지 보내면 하루 수십 장이라 추천건만. 상한은 STOCK_CHART_MAX(기본 5).
    """
    import os
    from stock_auto.notify.chart import render_signal_chart, caption_for
    recos = list(result.agents.recommendations)
    if not recos:
        return 0
    limit = int(os.environ.get("STOCK_CHART_MAX", "5") or 5)
    tg = telegram
    if tg is None:
        from stock_auto.config.env import get_secrets
        from stock_auto.notify.telegram import Telegram
        sec = get_secrets()
        tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
    rows = ([] if result.screen.candidates.empty
            else result.screen.candidates.to_dict("records"))
    by_sym = {str(r.get("symbol")): r for r in rows}
    sent = 0
    for i, reco in enumerate(recos[:limit]):
        # 추천-후보 매칭은 _record_signals와 같은 규칙(순서 우선, 티커 폴백)
        row = rows[i] if i < len(rows) else by_sym.get(str(reco.get("ticker")), {})
        sym = str(row.get("symbol") or reco.get("ticker") or "")
        df = ohlcv_map.get(sym)
        if df is None or df.empty:
            print(f"[batch] 차트 생략 {sym}: 일봉 없음")
            continue
        plan = {"stop": reco.get("stop"), "targets": reco.get("targets"),
                "close": row.get("close"), "recommend": reco.get("recommend"),
                "horizon": reco.get("horizon"), "conviction": reco.get("conviction"),
                "days_to_earnings": row.get("days_to_earnings")}
        png = render_signal_chart(sym, df, plan)
        if getattr(tg, "enabled", False):
            if tg.send_photo(png, caption_for(reco, row), filename=f"{sym}.png"):
                sent += 1
        else:
            from pathlib import Path
            out = Path("logs/charts"); out.mkdir(parents=True, exist_ok=True)
            (out / f"{sym}_{result.date}.png").write_bytes(png)
            print(f"[batch] 텔레그램 미설정 — 차트 저장 logs/charts/{sym}_{result.date}.png")
    print(f"[batch] 추천 차트 {sent}/{min(len(recos), limit)}장 전송")
    return sent


def _send_digest(result: BatchResult, telegram: Any = None) -> None:
    from stock_auto.notify.digest import batch_digest
    tg = telegram
    if tg is None:
        from stock_auto.config.env import get_secrets
        from stock_auto.notify.telegram import Telegram
        sec = get_secrets()
        tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
    text = batch_digest(result, result.sector_lines or None)
    if getattr(tg, "enabled", False):
        # parse_mode 없이 평문 — LLM 문장의 `_`·`*`가 Markdown 파싱을 깨뜨린다
        tg.send_message(text, parse_mode="")
        print(f"[batch] 텔레그램 다이제스트 전송 ({len(text)}자)")
    else:
        print("[batch] 텔레그램 미설정 — 다이제스트 콘솔 출력\n")
        print(text)


def _record_signals(result: BatchResult) -> int:
    """
    추천 후보를 신호 저장소에 적립 → labeler가 나중에 결과를 붙인다.

    기록 날짜는 실행일(result.date)이 아니라 '판단 근거가 된 마지막 봉의 날짜'다.
    라벨러는 df.index > date 구간을 전진 관찰하므로, 당일 봉이 아직 안 나온 상태에서
    실행일로 기록하면 전진 구간이 비어 영구 nodata가 된다.

    추천-후보 매칭은 티커 문자열이 아니라 '순서'로 한다. run_agents는 candidates를
    입력 순서대로 순회하며 append하므로 i번째 추천이 i번째 후보다. LLM이 ticker를
    다르게 적어도(공백·대소문자·오기) 계획 필드가 조용히 유실되지 않는다.
    """
    from stock_auto.tracking import store
    rows = ([] if result.screen.candidates.empty
            else result.screen.candidates.to_dict("records"))
    recos = list(result.agents.recommendations)
    by_ticker = {r.get("ticker"): r for r in recos}
    records = []
    for i, row in enumerate(rows):
        reco = recos[i] if i < len(recos) else by_ticker.get(row.get("symbol"))
        date = str(row.get("bar_date") or result.screen.as_of or result.date)
        records.append(store.from_screen_row(row, date, reco))

    # 게이트에 막힌 원신호도 동일하게 기록한다(LLM 추천은 없다).
    # 이게 없으면 하락장 구간 데이터가 통째로 비어 게이트의 실효성을 검증할 수 없다.
    gated = result.screen.gated
    if gated is not None and not gated.empty:
        for row in gated.to_dict("records"):
            date = str(row.get("bar_date") or result.screen.as_of or result.date)
            records.append(store.from_screen_row(row, date, None))
    _auto_execute(records)
    n = store.append(records)

    # 진입 트리거 적립 — 추천의 진입 조건을 감시 대상으로 등록한다.
    # 게이트 차단분은 제외(사람에게 보여준 적 없는 신호를 감시할 이유가 없다).
    try:
        _register_triggers(result, rows, recos, records)
    except Exception as e:  # noqa: BLE001 — 트리거 적립 실패가 배치를 막지 않게
        print(f"[batch] 트리거 적립 실패: {type(e).__name__}: {e}")
    return n


def _register_triggers(result: BatchResult, rows: list, recos: list,
                       records: list) -> int:
    """추천 1건당 진입 트리거 1건. 조건은 LLM 스키마 우선, 없으면 문장 파싱."""
    from stock_auto.tracking import triggers as T
    if not rows:
        return 0
    by_sym = {r.symbol: r for r in records}
    items = []
    for i, row in enumerate(rows):
        reco = recos[i] if i < len(recos) else None
        if not reco:
            continue
        date = str(row.get("bar_date") or result.screen.as_of or result.date)
        sym = str(row.get("symbol") or "")
        rec = by_sym.get(sym)
        items.append(T.from_recommendation(
            row, reco, date,
            signal_id=(rec.signal_id if rec else ""),
            sector_label=str(row.get("sector_label") or "")))
    added = T.append(items)
    if added:
        kinds: dict[str, int] = {}
        for t in items:
            kinds[t.kind] = kinds.get(t.kind, 0) + 1
        detail = " · ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
        print(f"[batch] 진입 트리거 {added}건 등록 ({detail})")
    return added


def _auto_execute(records: list) -> None:
    """
    STOCK_TRACK_AUTO_EXECUTE=yes|no 이면 게이트 통과 추천(AVOID 제외)의 실행 여부를 자동 표시.
    '전부 산다고 가정하고 한 달 지켜보기' 같은 관찰 모드용 — 매일 triage 입력을 생략한다.
    """
    import os
    mode = os.environ.get("STOCK_TRACK_AUTO_EXECUTE", "").strip().lower()
    if mode not in ("yes", "no"):
        return
    from stock_auto.config.clock import now_et
    stamp = now_et().strftime("%Y-%m-%d %H:%M %Z")
    for r in records:
        if r.source == "batch" and not r.gated_by and r.recommend and r.recommend != "AVOID":
            r.executed, r.executed_at = mode, stamp
            r.triage_note = "자동: 전부 매수 가정" if mode == "yes" else "자동: 미실행 가정"


def _publish_notion(notion, db_id, parent_page, result: BatchResult,
                    sector_label_map: dict) -> None:
    from stock_auto.integrations.notion_publisher import daily_summary_blocks
    # 게시 일자는 '판단 근거 봉'의 날짜 — 실행일과 다르면 제목에 함께 표기한다
    as_of = result.screen.as_of or result.date
    # 추천 행 — screener 행을 순서로 짝지어 함께 넘긴다.
    # Effective·종목명·섹터상태는 reco에 없거나 틀릴 수 있어 row가 정답이다
    # (RECO_SCHEMA에 effective_score가 없어 Effective가 전 건 비어 있었다).
    if db_id:
        cands = ([] if result.screen.candidates.empty
                 else result.screen.candidates.to_dict("records"))
        for i, reco in enumerate(result.agents.recommendations):
            row = cands[i] if i < len(cands) else {}
            sym = row.get("symbol") or reco.get("ticker", "-")
            notion.add_recommendation(
                db_id, reco, result.market.value, as_of,
                sector_label=sector_label_map.get(sym, "-"), row=row)
    # 일자 요약
    if parent_page:
        sl = result.screen.scored_all
        score_lines = [] if sl.empty else [
            f"{r.symbol}: Eff {r.effective_score:.2f} / 레짐 R{r.stock_regime}"
            for r in sl.head(10).itertuples()]
        ex = result.screen.exits
        exit_lines = [] if (ex is None or ex.empty) else [
            f"{r.symbol}: Exit_Score {r.exit_score}/5 · 종가 {r.close:,.2f}"
            for r in ex.itertuples()]
        blocks = daily_summary_blocks(
            result.screen.macro.label, result.screen.macro.level,
            score_lines, sector_lines=result.sector_lines or None,
            exit_lines=exit_lines or None)
        title = (f"[{result.market.value}] {result.date} 분석"
                 if as_of == result.date else
                 f"[{result.market.value}] {result.date} 분석 (기준봉 {as_of})")
        notion.create_summary_page(parent_page, title, blocks)
