"""
일일 배치 (Stage 1~6 + 게시) — US 마감 +1시간 실행.

흐름:
  1. 데이터 로드(일봉)           ← downloader (FDR/yfinance)
  2. 규칙 기반 전체 스캔(무토큰)  ← screener (+ 매크로 레짐, 섹터 게이트)
  3. 추천 상위 N LLM 분석        ← agents.pipeline (뉴스/수급/밸류/종합)
  4. Notion 게시(배치)           ← notion_publisher
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
    llm_client: Any = None,
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
        stock_sector_etf=stock_sector_etf, sector_status=sector_status)
    print(f"[batch] {market.value} 매크로 L{screen.macro.level}({screen.macro.label}) "
          f"| 후보 {len(screen.candidates)} | 실패 {len(screen.failures)}")

    result = BatchResult(market=market, date=today, screen=screen,
                         sector_lines=list(sector_lines or []))

    # 3) 추천 상위 N LLM 분석
    if run_llm and not screen.candidates.empty:
        result.agents = run_agents(
            screen.candidates, market, client=llm_client,
            sector_label_map=sector_label_map, top_n=top_n)
        print(f"[batch] 추천 {len(result.agents.recommendations)}건 생성")

    # 3.5) 결과 라벨링 루프: 추천 전 건을 기록 (실행 여부 무관 — 선택 편향 방지)
    try:
        n = _record_signals(result)
        print(f"[batch] 신호 기록 {n}건 적립 (라벨링 대기)")
    except Exception as e:  # noqa: BLE001 — 기록 실패가 배치를 막지 않게
        print(f"[batch] 신호 기록 실패: {type(e).__name__}: {e}")

    # 4) Notion 게시
    if notion is not None and getattr(notion, "enabled", False):
        _publish_notion(notion, notion_db_id, notion_parent_page,
                        result, sector_label_map or {})
    return result


def _record_signals(result: BatchResult) -> int:
    """추천 후보를 신호 저장소에 적립 → labeler가 나중에 결과를 붙인다."""
    from stock_auto.tracking import store
    if result.screen.candidates.empty:
        return 0
    by_ticker = {r.get("ticker"): r for r in result.agents.recommendations}
    records = [
        store.from_screen_row(row, result.date, by_ticker.get(row.get("symbol")))
        for row in result.screen.candidates.to_dict("records")
    ]
    return store.append(records)


def _publish_notion(notion, db_id, parent_page, result: BatchResult,
                    sector_label_map: dict) -> None:
    from stock_auto.integrations.notion_publisher import daily_summary_blocks
    # 추천 행
    if db_id:
        for reco in result.agents.recommendations:
            sym = reco.get("ticker", "-")
            notion.add_recommendation(
                db_id, reco, result.market.value, result.date,
                sector_label=sector_label_map.get(sym, "-"))
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
        notion.create_summary_page(
            parent_page, f"[{result.market.value}] {result.date} 분석", blocks)
