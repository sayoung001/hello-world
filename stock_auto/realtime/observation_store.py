"""
관측 저장소 — 실시간 모니터가 본 '시간대 누적거래량'을 매일 적립해
다음날 프로파일을 실측 기반으로 자가보정한다(피드백 루프의 핵심).

흐름:
  run_monitor 중 각 평가 윈도우(1·3·5분)에서 종목별 누적거래량을 1회 기록
    → data/observations/{market}.csv (date,symbol,window,cum_volume,cum_turnover)
  매일 밤 build_profiles --from-observations 가 이 누적을 읽어
    윈도우별 mean/std를 '실측'으로 재계산 → 정적 frac 곡선을 점차 대체.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path
from typing import Optional

from stock_auto.config.settings import Market
from stock_auto.realtime.volume_monitor import HistoricalProfile

_HEADER = ["date", "symbol", "window", "cum_volume", "cum_turnover"]


def _path(market: Market, base: str) -> Path:
    return Path(base) / f"{market.value}.csv"


def record(market: Market, date: str, symbol: str, window: int,
           cum_volume: float, cum_turnover: float = 0.0,
           base: str = "data/observations") -> None:
    """관측 1건 적립(append). 헤더 없으면 생성."""
    p = _path(market, base)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(_HEADER)
        w.writerow([date, symbol, int(window), f"{cum_volume:.0f}",
                    f"{cum_turnover:.0f}"])


def load(market: Market, base: str = "data/observations"
         ) -> dict[str, dict[int, list[tuple[str, float]]]]:
    """저장소 → {symbol: {window: [(date, cum_volume), ...]}}."""
    p = _path(market, base)
    out: dict[str, dict[int, list[tuple[str, float]]]] = defaultdict(
        lambda: defaultdict(list))
    if not p.exists():
        return out
    with p.open(newline="") as f:
        for row in csv.DictReader(f):
            try:
                out[row["symbol"]][int(row["window"])].append(
                    (row["date"], float(row["cum_volume"])))
            except (KeyError, ValueError):
                continue
    return out


def build_profiles_from_observations(
        market: Market, symbols: Optional[list[str]] = None,
        min_days: int = 10, base: str = "data/observations"
) -> dict[str, HistoricalProfile]:
    """
    실측 관측 → 윈도우별 (mean, std) 프로파일.
    종목별 '날짜 수'가 min_days 미만이면 신뢰 부족 → 빈 프로파일(호출측이 frac 폴백).
    같은 날짜 중복은 마지막 값으로 dedup(장중 윈도우 1회 기록 가정이나 안전장치).
    """
    data = load(market, base)
    syms = symbols or list(data.keys())
    profiles: dict[str, HistoricalProfile] = {}
    for s in syms:
        windows = data.get(s, {})
        buckets: dict[int, tuple[float, float]] = {}
        for w, recs in windows.items():
            by_date: dict[str, float] = {}
            for d, v in recs:
                by_date[d] = v
            vals = [v for v in by_date.values() if v > 0]
            if len(vals) >= min_days:
                mean = sum(vals) / len(vals)
                var = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
                buckets[w] = (mean, math.sqrt(var))
        if buckets:
            profiles[s] = HistoricalProfile(symbol=s, buckets=buckets)
    return profiles
