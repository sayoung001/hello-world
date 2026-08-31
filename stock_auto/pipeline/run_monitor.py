"""
개장 거래량 폭주 실시간 모니터 실행 (Stage 7).

사용:
  python -m stock_auto.pipeline.run_monitor --market KR
  python -m stock_auto.pipeline.run_monitor --market US --symbols AAPL NVDA TSLA

흐름: .env(KIS키) → KISFeed(WebSocket) → SurgeMonitor(폭주감지) → Telegram 알림.

유니버스: 사용자 정책상 '추천 위주' → --symbols 또는 추천파일 권장.
프로파일: 시간대 RVOL/Z는 HistoricalProfile 필요. 분봉 이력이 없으면 빈 프로파일로
          동작(RVOL/Z 게이트 비활성, 가격+거래대금+체결강도만 평가) — 경고 출력.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from stock_auto.config.env import get_secrets
from stock_auto.realtime.volume_monitor import (
    SurgeMonitor, HistoricalProfile, DEFAULT_THRESHOLDS, Market,
)
from stock_auto.data.kis_feed import KISFeed
from stock_auto.notify.telegram import Telegram, surge_alert_callback
from stock_auto.config.universe import load_universe


def _load_profiles(market: Market, symbols: list[str],
                   path: str = "data/profiles") -> dict[str, HistoricalProfile]:
    """저장된 시간대 프로파일 로드(JSON). 없으면 빈 프로파일 + 경고."""
    profiles: dict[str, HistoricalProfile] = {}
    base = Path(path) / market.value
    for sym in symbols:
        f = base / f"{sym}.json"
        if f.exists():
            buckets = {int(k): tuple(v) for k, v in
                       json.loads(f.read_text()).items()}
            profiles[sym] = HistoricalProfile(symbol=sym, buckets=buckets)
        else:
            profiles[sym] = HistoricalProfile(symbol=sym, buckets={})
    missing = [s for s in symbols if not profiles[s].buckets]
    if missing:
        print(f"⚠️ 프로파일 없음 {len(missing)}종목 → RVOL/Z 게이트 비활성. "
              "KIS 분봉 이력으로 build_profile_from_intraday 권장.")
    return profiles


def main() -> int:
    ap = argparse.ArgumentParser(description="거래량 폭주 실시간 모니터")
    ap.add_argument("--market", choices=["US", "KR"], default="KR")
    ap.add_argument("--symbols", nargs="*", help="감시 종목(미지정 시 유니버스 샘플)")
    ap.add_argument("--us-exchange", default="NAS", choices=["NAS", "NYS", "AMS"])
    ap.add_argument("--quantile", type=float, default=0.99,
                    help="적응 임계 분위수 (0.99 = 상위 1%%)")
    ap.add_argument("--fixed-thresholds", action="store_true",
                    help="적응 임계 끄고 고정 임계 사용")
    args = ap.parse_args()

    market = Market(args.market)
    sec = get_secrets()
    if not sec.has_kis:
        print("❌ KIS_APP_KEY / KIS_APP_SECRET 가 .env에 필요합니다.")
        return 1

    symbols = args.symbols or list(load_universe(market).keys())
    print(f"[monitor] {market.value} 감시 {len(symbols)}종목: {symbols}")

    profiles = _load_profiles(market, symbols)
    tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
    if not tg.enabled:
        print("⚠️ 텔레그램 미설정 — 알림은 콘솔로 출력")

    # 자가보정: 각 윈도우 누적거래량을 관측 저장소에 적립 → 다음날 프로파일 재구축
    # 날짜는 스냅샷의 거래소 시각(ET) 기준 → 서버 TZ 무관(시간대 버그 수정)
    from stock_auto.realtime import observation_store

    def _record(snap, window):
        observation_store.record(
            snap.market, snap.ts.strftime("%Y-%m-%d"), snap.symbol,
            window, snap.cum_volume, snap.cum_turnover)

    # 적응형 임계: 실측 RVOL 분포의 상위 분위수를 컷으로. 표본 부족 시 고정 임계 폴백
    provider = None
    if not args.fixed_thresholds:
        from stock_auto.realtime.adaptive_thresholds import build
        at = build(market, profiles, quantile=args.quantile)
        print("[monitor] " + at.summary())
        provider = at.provider()

    # 알림도 결과 라벨링 대상 — 정밀도를 측정하려면 전 건을 기록해야 한다
    from stock_auto.tracking import store as sig_store
    tg_send = surge_alert_callback(tg) if tg.enabled else (lambda s: print(s.to_telegram()))

    def _on_signal(sig):
        tg_send(sig)
        try:
            sig_store.append([sig_store.from_surge_signal(
                sig, sig.ts.strftime("%Y-%m-%d"))])
        except Exception as e:  # noqa: BLE001
            print(f"[monitor] 알림 기록 실패: {type(e).__name__}: {e}")

    monitor = SurgeMonitor(
        profiles, thresholds=DEFAULT_THRESHOLDS,
        on_signal=_on_signal, on_observation=_record,
        threshold_provider=provider)

    feed = KISFeed(sec.kis_app_key, sec.kis_app_secret, market,
                   env=sec.kis_env, us_exchange=args.us_exchange)
    print("[monitor] KIS WebSocket 연결 시작...")
    try:
        monitor.run(feed, symbols)
    except KeyboardInterrupt:
        print("\n[monitor] 종료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
