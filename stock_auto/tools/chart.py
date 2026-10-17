"""
수동 차트 확인 — 추천 종목(또는 아무 티커)의 일봉 차트를 그려 저장/전송한다.

사용:
  python -m stock_auto.tools.chart META               # logs/charts/META_<날짜>.png 저장
  python -m stock_auto.tools.chart META --send        # 텔레그램으로 전송
  python -m stock_auto.tools.chart META NVDA --send   # 여러 종목
  python -m stock_auto.tools.chart --today --send     # 오늘 기록된 추천 전부

계획선(손절/목표)은 signals.csv에 기록된 최신 값을 자동으로 찾아 얹는다.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from stock_auto.config.env import load_env, get_secrets
from stock_auto.config.settings import Market


def _latest_plan(symbol: str) -> dict:
    from stock_auto.tracking import store
    rows = [r for r in store.load() if r.get("symbol") == symbol and r.get("source") == "batch"]
    if not rows:
        return {}
    r = sorted(rows, key=lambda x: x.get("date", ""))[-1]
    return {"stop": r.get("stop"), "targets": [r.get("target")] if r.get("target") else [],
            "close": r.get("close"), "recommend": r.get("recommend"),
            "horizon": r.get("horizon"), "conviction": r.get("conviction"),
            "days_to_earnings": r.get("days_to_earnings"),
            "entry_rule": r.get("entry_rule"), "ticker": symbol}


def main() -> int:
    ap = argparse.ArgumentParser(description="추천 종목 차트")
    ap.add_argument("symbols", nargs="*", help="티커 (예: META NVDA)")
    ap.add_argument("--today", action="store_true", help="가장 최근 배치의 추천 전부")
    ap.add_argument("--send", action="store_true", help="텔레그램 전송")
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--bars", type=int, default=130)
    args = ap.parse_args()
    load_env()

    from stock_auto.data.downloader import load_or_download
    from stock_auto.notify.chart import render_signal_chart, caption_for
    market = Market(args.market)

    syms = list(args.symbols)
    if args.today:
        from stock_auto.tracking import store
        rows = [r for r in store.load() if r.get("source") == "batch"
                and r.get("recommend") and not r.get("gated_by")]
        if rows:
            last = max(r["date"] for r in rows)
            syms += [r["symbol"] for r in rows if r["date"] == last]
    if not syms:
        print("티커를 지정하거나 --today 를 쓰세요")
        return 1

    tg = None
    if args.send:
        from stock_auto.notify.telegram import Telegram
        sec = get_secrets()
        tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
        if not tg.enabled:
            print("텔레그램 미설정 — 파일로만 저장합니다")
            tg = None

    start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    out = Path("logs/charts"); out.mkdir(parents=True, exist_ok=True)
    for s in dict.fromkeys(syms):
        try:
            df = load_or_download(s, start, None, market)
        except Exception as e:  # noqa: BLE001
            print(f"{s}: 일봉 실패 {type(e).__name__}: {e}")
            continue
        plan = _latest_plan(s)
        png = render_signal_chart(s, df, plan, n_bars=args.bars)
        p = out / f"{s}_{datetime.now():%Y%m%d}.png"
        p.write_bytes(png)
        print(f"{s}: 저장 → {p}")
        if tg is not None:
            ok = tg.send_photo(png, caption_for(plan or {"ticker": s}), filename=f"{s}.png")
            print(f"{s}: 텔레그램 {'전송' if ok else '실패'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
