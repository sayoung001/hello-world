"""
적응형 임계 — 고정 RVOL 컷을 '실측 분포의 분위수'로 대체한다.

문제: RVOL ≥ 3.0 같은 고정 임계는 레짐이 바뀌면 무너진다.
      시장 전체가 조용한 날엔 알림이 하나도 안 뜨고,
      변동성 국면에선 전 종목이 동시에 뜬다.

해법: 관측 저장소에 쌓인 실제 RVOL 분포에서 상위 q 분위를 임계로 쓴다.
      "RVOL 3 이상"이 아니라 "평소 이 시간대 대비 상위 1%".
      표본이 부족하면 기존 고정 임계로 안전하게 폴백한다.

RVOL 재구성:
      관측(cum_volume) ÷ 프로파일 평균(mean) = 그날 그 윈도우의 RVOL
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, replace, field
from typing import Optional

from stock_auto.config.settings import Market
from stock_auto.realtime.volume_monitor import (
    SurgeThresholds, DEFAULT_THRESHOLDS, HistoricalProfile,
)

DEFAULT_QUANTILE = 0.99      # 상위 1%
MIN_SAMPLES = 50             # 이보다 적으면 고정 임계 사용
FLOOR_RVOL = 2.0             # 분위수가 아무리 낮아도 이 아래로는 안 내림
CEIL_RVOL = 12.0             # 과도한 임계 방지


@dataclass
class AdaptiveThresholds:
    """윈도우별 RVOL 컷을 실측 분포에서 산출해 제공한다."""
    base: dict[Market, SurgeThresholds] = field(
        default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    quantile: float = DEFAULT_QUANTILE
    min_samples: int = MIN_SAMPLES
    floor: float = FLOOR_RVOL
    ceil: float = CEIL_RVOL
    # {(market, window): (cut, n)}
    cuts: dict[tuple[str, int], tuple[float, int]] = field(default_factory=dict)

    # ── 적합 ──
    def fit(self, market: Market,
            profiles: dict[str, HistoricalProfile],
            base_path: str = "data/observations") -> dict[int, tuple[float, int]]:
        """관측 저장소 + 프로파일 → 윈도우별 RVOL 컷."""
        from stock_auto.realtime import observation_store as obs
        data = obs.load(market, base_path)

        by_window: dict[int, list[float]] = {}
        for sym, windows in data.items():
            prof = profiles.get(sym)
            if prof is None or not prof.buckets:
                continue
            for w, recs in windows.items():
                stat = prof.buckets.get(w)
                if not stat or stat[0] <= 0:
                    continue
                mean = stat[0]
                for _date, cum in recs:
                    if cum > 0:
                        by_window.setdefault(w, []).append(cum / mean)

        result: dict[int, tuple[float, int]] = {}
        for w, vals in by_window.items():
            cut = self._quantile_cut(vals)
            if cut is not None:
                self.cuts[(market.value, w)] = (cut, len(vals))
                result[w] = (cut, len(vals))
        return result

    def _quantile_cut(self, vals: list[float]) -> Optional[float]:
        if len(vals) < self.min_samples:
            return None
        vals = sorted(vals)
        idx = min(int(round(self.quantile * (len(vals) - 1))), len(vals) - 1)
        q = vals[idx]
        return float(min(max(q, self.floor), self.ceil))

    # ── 조회 ──
    def resolve(self, market: Market, window: int) -> SurgeThresholds:
        """해당 시장·윈도우의 임계. 적합 데이터 없으면 고정 임계 그대로."""
        base = self.base[market]
        hit = self.cuts.get((market.value, int(window)))
        if hit is None:
            return base
        cut, _n = hit
        return replace(base, rvol_min=cut)

    def provider(self):
        """SurgeMonitor(threshold_provider=...)에 꽂을 콜러블."""
        return lambda market, window: self.resolve(market, window)

    # ── 진단 ──
    def summary(self) -> str:
        if not self.cuts:
            return "적응 임계 없음 — 표본 부족으로 고정 임계 사용"
        lines = ["적응 임계 (실측 분위수 기반)"]
        for (mk, w), (cut, n) in sorted(self.cuts.items()):
            lines.append(f"  {mk} {w}분 윈도우: RVOL ≥ {cut:.2f} "
                         f"(상위 {(1 - self.quantile) * 100:.0f}%, 표본 {n})")
        return "\n".join(lines)


def build(market: Market, profiles: dict[str, HistoricalProfile],
          quantile: float = DEFAULT_QUANTILE,
          base_path: str = "data/observations") -> AdaptiveThresholds:
    """편의 함수: 적합까지 한 번에."""
    at = AdaptiveThresholds(quantile=quantile)
    at.fit(market, profiles, base_path)
    return at
