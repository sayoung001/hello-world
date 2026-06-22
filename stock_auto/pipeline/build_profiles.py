"""
시간대 프로파일 구축 실행 (C).

사용:
  python -m stock_auto.pipeline.build_profiles --market KR
  python -m stock_auto.pipeline.build_profiles --market US --symbols AAPL NVDA --days 90

일봉 거래량(다운로드) × 장중 분포곡선 → data/profiles/{market}/{sym}.json 저장.
이후 run_monitor가 이 파일을 읽어 RVOL/Z 게이트를 활성화한다.
"""

from __future__ import annotations

import argparse

from stock_auto.config.settings import Market
from stock_auto.config.universe import load_universe
from stock_auto.realtime.profile_builder import build_and_save


def main() -> int:
    ap = argparse.ArgumentParser(description="시간대 거래량 프로파일 구축")
    ap.add_argument("--market", choices=["US", "KR"], default="KR")
    ap.add_argument("--symbols", nargs="*", help="대상(미지정 시 유니버스 샘플)")
    ap.add_argument("--days", type=int, default=90, help="프로파일 산출 일수")
    args = ap.parse_args()

    market = Market(args.market)
    symbols = args.symbols or list(load_universe(market).keys())
    print(f"[profiles] {market.value} {len(symbols)}종목 구축(최근 {args.days}일)...")

    result = build_and_save(symbols, market, lookback_days=args.days)
    if result:
        print(f"✅ 저장 {len(result)}종목 → data/profiles/{market.value}/")
        for s, n in list(result.items())[:10]:
            print(f"   {s}: {n}버킷")
    else:
        print("❌ 생성 실패 (일봉 데이터 확인 — 네트워크/심볼)")
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
