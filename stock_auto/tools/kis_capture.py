"""
KIS 원시 패킷 캡처 (필드 정렬 검증).

사용:
  python -m stock_auto.tools.kis_capture --market KR --symbols 005930 --n 5
  python -m stock_auto.tools.kis_capture --market US --symbols AAPL --n 5 --us-exchange NAS

장중에 실행해 처음 N개 실시간 체결 패킷을 그대로 출력한다.
출력된 '0|H0STCNT0|001|<body>' 의 body(^ 구분)를 보내주시면 필드 인덱스를 정확히
맞춰 드립니다(KIS 명세 개정 대비).
"""

from __future__ import annotations

import argparse

from stock_auto.config.env import get_secrets
from stock_auto.realtime.volume_monitor import Market
from stock_auto.data.kis_feed import KISFeed, KR_IDX, US_IDX


def main() -> int:
    ap = argparse.ArgumentParser(description="KIS 원시 패킷 캡처")
    ap.add_argument("--market", choices=["US", "KR"], default="KR")
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--n", type=int, default=5, help="캡처 패킷 수")
    ap.add_argument("--us-exchange", default="NAS", choices=["NAS", "NYS", "AMS"])
    args = ap.parse_args()

    sec = get_secrets()
    if not sec.has_kis:
        print("❌ KIS 키가 .env에 필요합니다.")
        return 1

    market = Market(args.market)
    idx = KR_IDX if market == Market.KR else US_IDX
    print(f"[capture] {market.value} {args.symbols} — {args.n}패킷 캡처")
    print(f"[capture] 현재 매핑 인덱스: {idx}")
    print("[capture] 아래 [kis-raw] 의 body(^구분)를 보내주시면 인덱스 정렬 드립니다.\n")

    captured = {"n": 0}
    feed = KISFeed(sec.kis_app_key, sec.kis_app_secret, market,
                   env=sec.kis_env, us_exchange=args.us_exchange,
                   debug_raw=args.n, should_stop=lambda: captured["n"] >= args.n)
    for _snap in feed.stream(args.symbols):
        captured["n"] += 1
        if captured["n"] >= args.n:
            break
    print("\n[capture] 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
