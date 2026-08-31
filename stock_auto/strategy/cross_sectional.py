"""
횡단면 모멘텀 — 유니버스 내 상대 순위 피처.

배경: Gu·Kelly·Xiu(2020)는 대규모 ML 비교에서 가장 중요한 예측 축으로
      모멘텀 · 유동성 · 변동성을 지목했다. 현 시스템은 유동성(Liquidity Score)과
      변동성(ATR)은 갖췄으나 모멘텀이 ROC 12 하나뿐이라 약했다.

구현:
  - 12-1 모멘텀 (Jegadeesh & Titman 1993): t-252 → t-21 수익률.
    최근 1개월을 건너뛰는 이유는 단기 반전 효과와 섞이지 않게 하기 위함.
  - 변동성 조정 모멘텀 (Barroso & Santa-Clara): 모멘텀 크래시 완화.
  - 횡단면 백분위 순위(0~1) — 절대값보다 안정적이며 레짐 변화에 강건.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

LOOKBACK_LONG = 252    # 12개월
LOOKBACK_MID = 126     # 6개월
SKIP_RECENT = 21       # 최근 1개월 제외
VOL_WINDOW = 126


def _ret(close: pd.Series, start_back: int, end_back: int) -> Optional[float]:
    """close[-end_back] / close[-start_back] - 1. 데이터 부족 시 None."""
    if len(close) < start_back + 1:
        return None
    a = float(close.iloc[-start_back])
    b = float(close.iloc[-end_back]) if end_back > 0 else float(close.iloc[-1])
    if a <= 0:
        return None
    return b / a - 1.0


def symbol_momentum(df: pd.DataFrame) -> dict[str, Optional[float]]:
    """단일 종목의 모멘텀 지표들."""
    if df is None or "Close" not in df.columns or len(df) < 40:
        return {"mom_12_1": None, "mom_6_1": None, "vol_ann": None,
                "mom_vol_adj": None}
    c = df["Close"].astype(float)

    mom_12_1 = _ret(c, LOOKBACK_LONG, SKIP_RECENT)
    mom_6_1 = _ret(c, LOOKBACK_MID, SKIP_RECENT)
    # 이력이 짧으면 가능한 가장 긴 구간으로 대체(랭크 비교 가능성 유지)
    if mom_12_1 is None and mom_6_1 is None:
        span = min(len(c) - 1, 60)
        if span > SKIP_RECENT + 5:
            mom_6_1 = _ret(c, span, SKIP_RECENT)

    rets = c.pct_change().dropna()
    vol_ann = None
    if len(rets) >= 20:
        vol_ann = float(rets.tail(VOL_WINDOW).std() * np.sqrt(252))

    base = mom_12_1 if mom_12_1 is not None else mom_6_1
    mom_vol_adj = None
    if base is not None and vol_ann and vol_ann > 1e-6:
        mom_vol_adj = base / vol_ann

    return {"mom_12_1": mom_12_1, "mom_6_1": mom_6_1,
            "vol_ann": vol_ann, "mom_vol_adj": mom_vol_adj}


def momentum_features(ohlcv_map: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """
    유니버스 전체 → 종목별 모멘텀 + 횡단면 백분위 순위.
    반환: {symbol: {mom_12_1, mom_6_1, vol_ann, mom_vol_adj, mom_rank, mom_rank_vadj}}
    """
    raw: dict[str, dict] = {}
    for sym, df in ohlcv_map.items():
        raw[sym] = symbol_momentum(df)

    _add_rank(raw, "mom_12_1", "mom_rank", fallback="mom_6_1")
    _add_rank(raw, "mom_vol_adj", "mom_rank_vadj")
    return raw


def _add_rank(raw: dict[str, dict], key: str, out_key: str,
              fallback: Optional[str] = None) -> None:
    """유효값들에 대해 0~1 백분위 순위를 매긴다(동점은 평균 순위)."""
    vals: dict[str, float] = {}
    for sym, d in raw.items():
        v = d.get(key)
        if v is None and fallback:
            v = d.get(fallback)
        if v is not None and np.isfinite(v):
            vals[sym] = float(v)
    if len(vals) < 2:
        for sym in raw:
            raw[sym][out_key] = None
        return
    s = pd.Series(vals)
    ranks = s.rank(pct=True, method="average")
    for sym in raw:
        raw[sym][out_key] = float(ranks[sym]) if sym in ranks.index else None
