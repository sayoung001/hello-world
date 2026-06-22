"""
시간대 거래량 프로파일 구축 (C) — RVOL/Z 게이트 활성화.

핵심: 실시간 RVOL = (오늘 개장후 N분 누적거래량) / (과거 같은 시간대 누적거래량 평균).
과거 '분 단위 누적거래량'을 직접 모으려면 분봉 이력이 필요한데 KIS 분봉 이력은 제약이 크다.

대안(견고·검증가능):
  과거 일봉 총거래량(다운로더로 안정적 확보) × '장중 누적거래량 분포곡선(frac)'
  = 과거 각 일자의 시간대별 기대 누적거래량 → 그 분포로 mean/std 추정.

frac(개장후 m분까지 누적된 일일거래량 비율)은 시장별 기본 U자형 곡선을 제공하고,
실측 분봉이 있으면 calibrate_fraction으로 보정한다(선택).
→ 시작 임계는 약간의 편차를 감안해도 RVOL≥3 게이트가 견고하게 동작.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

from stock_auto.config.settings import Market
from stock_auto.realtime.volume_monitor import HistoricalProfile

# 개장 후 분(min) → 그 시점까지 누적된 일일거래량 비율(frac) 기본곡선.
# 장 초반이 무겁고 중반이 가벼운 전형적 패턴(첫 30분 ≈ 18~19%).
_DEFAULT_CURVE: dict[int, float] = {
    1: 0.012, 2: 0.022, 3: 0.032, 4: 0.040, 5: 0.048,
    10: 0.085, 15: 0.115, 20: 0.140, 30: 0.190,
}


def fraction_at(minute: float, curve: Optional[dict[int, float]] = None) -> float:
    """곡선에서 개장후 minute의 누적 비율(선형보간)."""
    curve = curve or _DEFAULT_CURVE
    keys = sorted(curve)
    if minute <= keys[0]:
        return curve[keys[0]] * (minute / keys[0]) if keys[0] else curve[keys[0]]
    if minute >= keys[-1]:
        return curve[keys[-1]]
    for a, b in zip(keys, keys[1:]):
        if a <= minute <= b:
            t = (minute - a) / (b - a)
            return curve[a] + t * (curve[b] - curve[a])
    return curve[keys[-1]]


def build_profile(symbol: str, daily_volumes: list[float],
                  windows=(1, 3, 5, 10, 15, 30),
                  curve: Optional[dict[int, float]] = None) -> HistoricalProfile:
    """
    과거 일봉 총거래량 리스트 → 시간대 버킷별 (mean, std) 프로파일.
    expected_cum[day][w] = daily_volume[day] * fraction_at(w)
    """
    buckets: dict[int, tuple[float, float]] = {}
    vols = [v for v in daily_volumes if v and v > 0]
    if len(vols) < 2:
        return HistoricalProfile(symbol=symbol, buckets={})
    for w in windows:
        frac = fraction_at(w, curve)
        vals = [v * frac for v in vols]
        mean = sum(vals) / len(vals)
        var = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
        buckets[int(w)] = (mean, math.sqrt(var))
    return HistoricalProfile(symbol=symbol, buckets=buckets)


def calibrate_fraction(intraday_sessions: list[list[tuple[int, float]]]
                       ) -> dict[int, float]:
    """
    (선택) 실측 분봉으로 frac 곡선 보정.
    intraday_sessions: 일자별 [(개장후분, 그분까지 누적거래량), ...].
    각 일자의 누적/일총량 비율을 분 버킷별 평균.
    """
    by_bucket: dict[int, list[float]] = {}
    for day in intraday_sessions:
        if not day:
            continue
        day_total = max(v for _, v in day)
        if day_total <= 0:
            continue
        for mn, cum in day:
            by_bucket.setdefault(int(mn), []).append(cum / day_total)
    return {b: sum(v) / len(v) for b, v in by_bucket.items() if v}


# ── 저장/로드 (run_monitor 로더와 호환: {bucket: [mean,std]}) ──
def save_profile(profile: HistoricalProfile, market: Market,
                 base: str = "data/profiles") -> Path:
    d = Path(base) / market.value
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{profile.symbol}.json"
    path.write_text(json.dumps({str(k): [round(m, 2), round(s, 2)]
                                for k, (m, s) in profile.buckets.items()}))
    return path


def build_and_save(symbols: list[str], market: Market,
                   lookback_days: int = 90,
                   curve: Optional[dict[int, float]] = None,
                   ohlcv_map: Optional[dict] = None) -> dict[str, int]:
    """
    유니버스 일봉 다운로드 → 프로파일 생성·저장.
    ohlcv_map 주입 시 다운로드 생략(테스트). 반환: {symbol: 버킷수}.
    """
    from datetime import datetime, timedelta
    out: dict[str, int] = {}
    if ohlcv_map is None:
        from stock_auto.data.downloader import load_or_download
        start = (datetime.now() - timedelta(days=lookback_days * 2)).strftime("%Y-%m-%d")
        ohlcv_map = {}
        for s in symbols:
            try:
                ohlcv_map[s] = load_or_download(s, start, None, market)
            except Exception as e:  # noqa: BLE001
                print(f"[profile] {s} 일봉 실패: {type(e).__name__}: {e}")
    for s in symbols:
        df = ohlcv_map.get(s)
        if df is None or "Volume" not in df.columns:
            continue
        vols = df["Volume"].tail(lookback_days).tolist()
        prof = build_profile(s, vols, curve=curve)
        if prof.buckets:
            save_profile(prof, market)
            out[s] = len(prof.buckets)
    return out
