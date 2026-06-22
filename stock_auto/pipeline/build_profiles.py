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
from stock_auto.realtime.profile_builder import build_and_save, save_profile
from stock_auto.realtime import observation_store


def main() -> int:
    ap = argparse.ArgumentParser(description="시간대 거래량 프로파일 구축")
    ap.add_argument("--market", choices=["US", "KR"], default="KR")
    ap.add_argument("--symbols", nargs="*", help="대상(미지정 시 유니버스 샘플)")
    ap.add_argument("--days", type=int, default=90, help="일봉 frac 산출 일수")
    ap.add_argument("--from-observations", action="store_true",
                    help="실측 관측 우선(자가보정). 데이터 부족분만 일봉 frac 폴백")
    ap.add_argument("--min-days", type=int, default=10,
                    help="실측 신뢰 최소 일수(미만이면 frac 폴백)")
    args = ap.parse_args()

    market = Market(args.market)
    symbols = args.symbols or list(load_universe(market).keys())

    empirical: dict[str, int] = {}
    if args.from_observations:
        profs = observation_store.build_profiles_from_observations(
            market, symbols, min_days=args.min_days)
        for s, prof in profs.items():
            save_profile(prof, market)
            empirical[s] = len(prof.buckets)
        print(f"[profiles] 실측 기반 {len(empirical)}종목 (min_days={args.min_days})")
        # 실측 부족 종목만 frac 폴백
        symbols = [s for s in symbols if s not in empirical]

    fallback = build_and_save(symbols, market, lookback_days=args.days) if symbols else {}
    total = {**empirical, **fallback}
    if total:
        print(f"✅ 저장 {len(total)}종목 → data/profiles/{market.value}/ "
              f"(실측 {len(empirical)} / frac {len(fallback)})")
        for s, n in list(total.items())[:10]:
            tag = "실측" if s in empirical else "frac"
            print(f"   {s}: {n}버킷 [{tag}]")
    else:
        print("❌ 생성 실패 (일봉/관측 데이터 확인)")
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
