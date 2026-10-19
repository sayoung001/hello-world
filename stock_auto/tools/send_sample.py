"""
텔레그램 샘플 발송 — 실제로 어떤 메시지가 오는지 미리 본다.

왜 필요한가:
  이 시스템이 폰으로 보내는 메시지는 4종류인데, 배치가 돌거나 트리거가
  발화할 때까지 기다려야 생김새를 볼 수 없었다. 그래서 (a) 텔레그램 배선이
  살아 있는지, (b) 잘림·이모지·평문 처리가 제대로 되는지를 사고가 난 뒤에야
  알게 된다. 샘플을 미리 받아 보면 둘 다 먼저 걸러진다.

  메시지 본문은 **실제 코드가 만든다**(digest.batch_digest,
  trigger_notify.short_alert 등). 따로 적은 예시 문구를 보내면 실제와
  달라져서 미리 보는 의미가 없다.

사용:
  .venv/bin/python -m stock_auto.tools.send_sample              # 전부 발송
  .venv/bin/python -m stock_auto.tools.send_sample --dry-run    # 콘솔만(키 불필요)
  .venv/bin/python -m stock_auto.tools.send_sample --only digest
  .venv/bin/python -m stock_auto.tools.send_sample --real       # 실제 누적 기록으로

종류:
  digest   일일 추천 다이제스트(매크로·섹터·추천·게이트차단·매도신호·비용)
  trigger  진입 조건 도달 알림(노션 링크 포함 단축 알림)
  autopull 자동 반영 중단 알림
  usage    토큰 사용량·비용 한 줄
"""

from __future__ import annotations

import argparse
from typing import Any, Optional

KINDS = ("digest", "trigger", "autopull", "usage")
PREFIX = "🧪 [샘플] 실제 발송 형식 미리보기 — 실제 추천이 아닙니다\n\n"


# ──────────────────────────────────────────────────────────────────────────
# 샘플 입력 — 실제 파이프라인이 넘기는 모양 그대로
# ──────────────────────────────────────────────────────────────────────────
def _sample_screen_result() -> Any:
    """screener.ScreenResult 와 같은 모양의 가벼운 대역."""
    import pandas as pd
    from stock_auto.config.settings import Market
    from stock_auto.strategy.macro_regime import MacroRegime

    cands = pd.DataFrame([
        {"symbol": "NVDA", "bar_date": "2026-10-19", "close": 182.50,
         "effective_score": 4.55, "money_score": 2.0, "price_score": 3.0,
         "liquidity_score": 1.28, "penalty": 0.0, "stock_regime": 3,
         "macro_regime": 2, "sector_etf": "XLK", "sector_status_score": 4,
         "strategies": "VolBreakout,MoneyFlowSurge", "styles": "단타,중단기",
         "stop": 174.20, "target": 196.00, "rr_ratio": 1.73, "atr": 5.4,
         "rsi_14": 61.2, "adx": 34.1, "rvol": 2.3, "final_buy": True,
         "raw_buy": True, "gated_by": "", "days_to_earnings": 18},
        {"symbol": "LLY", "bar_date": "2026-10-19", "close": 884.10,
         "effective_score": 3.90, "money_score": 1.5, "price_score": 2.5,
         "liquidity_score": 1.10, "penalty": 0.0, "stock_regime": 2,
         "macro_regime": 2, "sector_etf": "XLV", "sector_status_score": 3,
         "strategies": "IchimokuTrend", "styles": "스윙",
         "stop": 845.00, "target": 952.00, "rr_ratio": 1.74, "atr": 22.4,
         "rsi_14": 57.0, "adx": 28.3, "rvol": 1.4, "final_buy": True,
         "raw_buy": True, "gated_by": "", "days_to_earnings": None},
    ])
    gated = pd.DataFrame([
        {"symbol": "AAPL", "bar_date": "2026-10-19", "effective_score": 3.55,
         "gated_by": "earnings", "days_to_earnings": 2},
        {"symbol": "XOM", "bar_date": "2026-10-19", "effective_score": 2.80,
         "gated_by": "sector", "days_to_earnings": None},
    ])
    exits = pd.DataFrame([
        {"symbol": "TSLA", "exit_score": 3, "close": 412.30},
    ])

    class _S:
        as_of = "2026-10-19"
        candidates = cands
        scored_all = cands
        failures: dict = {}

    s = _S()
    s.gated = gated
    s.exits = exits
    # label·detail 은 per_index 에서 파생되는 property 다 — 직접 넣을 수 없다.
    # 실제 산출 결과와 같은 모양(지수별 레짐)으로 만들어야 샘플이 실제와 같다.
    s.macro = MacroRegime(
        market=Market.US, level=3,
        per_index={"US500": 3, "IXIC": 2, "QQQ": 3},
        per_group={"US500": 3, "IXIC,QQQ": 3})
    return s


def _sample_recos() -> list[dict]:
    return [
        {"ticker": "NVDA", "recommend": "BUY", "horizon": "중단기",
         "conviction": 0.68,
         "entry_rule": "추천일 종가 대비 1.5% 눌림 시 분할 진입 (RVOL>1.5 확인)",
         "entry_trigger": {"kind": "pullback", "value_pct": 1.5,
                           "basis": "signal_close", "split": True},
         "entry_zone": [179.8, 182.5], "stop": 174.20, "targets": [196.00],
         "expected_pnl_today": {"p10_pct": -2.4, "p50_pct": 0.6, "p90_pct": 3.1,
                                "basis": "ATR 5.4(2.9%) · R:R 1.73 기준 분포"},
         "exit_plan": "목표 196 도달 시 절반 청산, 잔량은 20일선 이탈까지 보유",
         "rationale": {"sector": "XLK 양호(4) — 기술 섹터 자금 유입 지속",
                       "signal": "Effective 4.55 · 거래대금 급증 동반 돌파",
                       "news": "블랙웰 수요 강세 리포트 다수",
                       "flow": "RVOL 2.3 · 단발 펌핑이 아닌 매집 패턴",
                       "value": "RSI 61 과열 아님 · 20일선 위 지지"},
         "sources": ["https://example.com/news/1"]},
        {"ticker": "LLY", "recommend": "WATCH", "horizon": "스윙",
         "conviction": 0.52,
         "entry_rule": "전일 종가 0.5% 위 돌파 확인 후 진입",
         "entry_trigger": {"kind": "breakout", "value_pct": 0.5,
                           "basis": "prev_close", "split": False},
         "entry_zone": [884.1], "stop": 845.00, "targets": [952.00],
         "expected_pnl_today": {"p10_pct": -1.8, "p50_pct": 0.3, "p90_pct": 2.2,
                                "basis": "ATR 22.4(2.5%) 기준"},
         "exit_plan": "손절 845 이탈 시 전량, 목표 952 도달 시 절반",
         "rationale": {"sector": "XLV 보통(3)", "signal": "일목 추세 전환",
                       "news": "수집된 뉴스 없음", "flow": "RVOL 1.4 완만",
                       "value": "적정 구간"},
         "sources": []},
    ]


def _sample_trigger() -> dict:
    return {
        "trigger_id": "sample1", "signal_id": "sample1", "date": "2026-10-19",
        "market": "US", "symbol": "NVDA", "kind": "pullback", "value_pct": 1.5,
        "basis": "signal_close", "split": "yes", "basis_price": 182.50,
        "target_price": 179.76,
        "rule_text": "추천일 종가 대비 1.5% 눌림 시 분할 진입",
        "horizon": "중단기", "recommend": "BUY", "conviction": 0.68,
        "sector_etf": "XLK", "sector_label": "양호", "effective_score": 4.55,
        "strategies": "VolBreakout,MoneyFlowSurge", "stop": 174.20,
        "target": 196.00,
        "rationale": "XLK 양호 · 거래대금 급증 동반 돌파 / RVOL 2.3 매집 패턴",
    }


# ──────────────────────────────────────────────────────────────────────────
# 본문 생성 — 실제 코드를 쓴다
# ──────────────────────────────────────────────────────────────────────────
def build(kind: str, real: bool = False) -> str:
    if kind == "digest":
        from stock_auto.config.settings import Market
        from stock_auto.notify.digest import batch_digest

        class _R:
            market = Market.US
            date = "2026-10-19"
            sector_lines: list = []
        r = _R()
        r.screen = _sample_screen_result()
        r.agents = type("A", (), {"recommendations": _sample_recos()})()
        body = batch_digest(r, ["XLK 양호(4) — 자금 유입",
                                "XLV 보통(3)",
                                "XLE 주의(1) — 매수 차단"])
        # 비용 한 줄을 붙여 실제 운영 모습에 가깝게
        try:
            from stock_auto.tracking import usage_log
            line = usage_log.telegram_line()
            if line:
                body += "\n" + line
        except Exception:  # noqa: BLE001
            pass
        return body

    if kind == "trigger":
        from stock_auto.tracking.trigger_notify import short_alert
        return short_alert(_sample_trigger(), 179.72,
                           "https://www.notion.so/00000000000000000000000000000000")

    if kind == "autopull":
        import socket
        host = socket.gethostname().split(".")[0]
        return (f"🚨 자동 반영 멈춤 — {host}\n\n"
                "코드는 받았지만 데몬 재시작이 막혔습니다 — 서버가 계속 옛 코드로 돕니다.\n"
                "비밀번호 없는 sudo 를 등록하세요:\n"
                '  echo "$(whoami) ALL=(ALL) NOPASSWD: /bin/systemctl restart stock-auto"'
                " | sudo tee /etc/sudoers.d/stock-auto\n"
                "  sudo chmod 440 /etc/sudoers.d/stock-auto\n\n"
                "이 상태로는 깃허브에 올린 수정이 서버에 반영되지 않습니다.\n"
                "확인:  tail -30 ~/auto_trade/logs/autopull.log")

    if kind == "usage":
        from stock_auto.tracking import usage_log
        if real:
            line = usage_log.telegram_line()
            if line:
                return line
            return ("💰 LLM 비용 — 아직 기록이 없습니다. "
                    "LLM을 켠 배치가 한 번 돌면 쌓입니다.")
        # 실측 규모(5종목)로 환산해 보여준다
        usage = {"input_tokens": 8050, "output_tokens": 15225}
        from stock_auto.agents.base import DEFAULT_MODEL
        cost = usage_log.estimate_cost(DEFAULT_MODEL, usage)
        if cost is None:
            return f"💰 LLM 비용 — 단가 미등록 모델({DEFAULT_MODEL})"
        return (f"💰 LLM 비용 (샘플 환산)\n"
                f"모델 {DEFAULT_MODEL}\n"
                f"입력 {usage['input_tokens']:,} tok · 출력 {usage['output_tokens']:,} tok\n"
                f"배치당 ${cost:.4f} · 월 환산 ${cost * 21:.2f} (21영업일)")

    raise ValueError(f"알 수 없는 종류: {kind}")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="텔레그램 샘플 발송")
    ap.add_argument("--only", choices=KINDS, action="append",
                    help="이 종류만 발송(여러 번 지정 가능). 기본은 전부")
    ap.add_argument("--dry-run", action="store_true",
                    help="발송하지 않고 콘솔에만 출력(키 불필요)")
    ap.add_argument("--real", action="store_true",
                    help="usage 는 실제 누적 기록으로 생성")
    ap.add_argument("--no-prefix", action="store_true",
                    help="'[샘플]' 머리말을 붙이지 않음")
    args = ap.parse_args(argv)

    kinds = args.only or list(KINDS)
    tg = None
    if not args.dry_run:
        from stock_auto.config.env import get_secrets
        from stock_auto.notify.telegram import Telegram
        sec = get_secrets()
        tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
        if not tg.enabled:
            print("❌ 텔레그램 미설정 — .env 의 TELEGRAM_BOT_TOKEN / "
                  "TELEGRAM_CHAT_ID 를 확인하세요.")
            print("   형식만 보려면: --dry-run")
            return 1

    sent = 0
    for kind in kinds:
        try:
            body = build(kind, real=args.real)
        except Exception as e:  # noqa: BLE001 — 한 종류 실패가 나머지를 막지 않게
            print(f"[{kind}] 생성 실패: {type(e).__name__}: {e}")
            continue
        text = body if args.no_prefix else PREFIX + body
        if args.dry_run:
            print("\n" + "─" * 60)
            print(f"  [{kind}]  {len(text)}자")
            print("─" * 60)
            print(text)
            continue
        # parse_mode 는 빈 문자열(평문). LLM 문장에 _ * [ 가 섞이면
        # Markdown 파싱이 깨져 메시지 전체가 실패한다.
        ok = tg.send_message(text, parse_mode="")
        print(f"[{kind}] {'발송 완료' if ok else '발송 실패'} ({len(text)}자)")
        sent += int(bool(ok))

    if not args.dry_run:
        print(f"\n{sent}/{len(kinds)}건 발송 — 폰에서 확인하세요.")
        return 0 if sent == len(kinds) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
