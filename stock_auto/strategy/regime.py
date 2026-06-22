"""
레짐 판정 (US_KR_)CLAUD regime_v2.py 계승 + ★매크로 지수 레짐 신설).

분석 §3.7: 기존은 개별 종목 MA 배열로만 레짐 산출 → 약세장에서도 개별 종목은
정배열일 수 있어 시장 위험을 놓침. SPY/QQQ(미), KOSPI/KOSDAQ(국) 지수 레짐을
별도 산출해 종목 레짐과 AND 게이트로 결합한다.

이는 코인봇 auto_trader_v9의 'BTC 7단계 필터'를 주식 지수 레짐으로 번역한 것이다.
  BTC가 시장 전체 위험을 게이팅 → 주식은 지수가 그 역할.

레짐 5단계: 0 위험 / 1 약세 / 2 중립 / 3 강세 / 4 강한상승
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from stock_auto.config.settings import Market, get_config
from stock_auto.indicators.indicators import _ema, _adx


REGIME_NAMES = {0: "위험", 1: "약세", 2: "중립", 3: "강세", 4: "강한상승"}


def _regime_from_df(df: pd.DataFrame) -> int:
    """단일 시계열(종목 또는 지수)의 5단계 레짐."""
    c = df["Close"]
    ema20 = _ema(c, 20)
    ema50 = _ema(c, 50)
    ema200 = _ema(c, 200)
    adx = _adx(df)
    last = -1
    px = c.iloc[last]
    e20, e50, e200 = ema20.iloc[last], ema50.iloc[last], ema200.iloc[last]
    up_slope = ema20.iloc[last] > ema20.iloc[last - 5]

    if px < e200 and e50 < e200:
        return 0  # 위험 (장기 하락 배열)
    if px < e50:
        return 1  # 약세
    if e20 > e50 > e200 and up_slope and adx.iloc[last] >= 25:
        return 4  # 강한상승
    if e20 > e50 and px > e50:
        return 3  # 강세
    return 2      # 중립


@dataclass
class MacroRegime:
    market: Market
    level: int                 # 지수 종합 레짐 (구성 지수 중 최소)
    per_index: dict[str, int]  # 지수별 레짐

    @property
    def name(self) -> str:
        return REGIME_NAMES[self.level]

    @property
    def trade_allowed(self) -> bool:
        """레짐 0(위험)이면 전 종목 매수 보류 (AND 게이트 차단)."""
        return self.level >= 1


def compute_macro_regime(index_data: dict[str, pd.DataFrame],
                         market: Market) -> MacroRegime:
    """
    지수별 일봉 dict → 매크로 레짐.
    종합 레짐은 보수적으로 '구성 지수 중 최소값'을 채택(가장 약한 지수 기준).
    """
    cfg = get_config(market)
    per = {}
    for sym in cfg.index_symbols:
        if sym in index_data and len(index_data[sym]) >= 200:
            per[sym] = _regime_from_df(index_data[sym])
    level = min(per.values()) if per else 2  # 데이터 없으면 중립 가정
    return MacroRegime(market=market, level=level, per_index=per)


def stock_regime(df: pd.DataFrame) -> int:
    """개별 종목 레짐."""
    return _regime_from_df(df)


def regime_gate(stock_lvl: int, macro: MacroRegime) -> bool:
    """
    AND 게이트 (분석 §3.7):
      - 매크로 레짐 0(위험) → 무조건 차단
      - 종목 레짐 0 → 차단
      - 그 외 통과
    """
    if not macro.trade_allowed:
        return False
    if stock_lvl <= 0:
        return False
    return True
