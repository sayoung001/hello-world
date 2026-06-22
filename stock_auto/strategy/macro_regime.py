"""
매크로 지수 레짐 + AND 게이트 (분석 §3.7 신규 보완).

기존 US_KR_)CLAUD의 레짐(regime_v2.add_market_regime)은 '개별 종목' MA 배열로만
산출돼 약세장에서도 개별 종목은 정배열일 수 있어 시장 위험을 놓친다.

여기서는 지수(SPY/QQQ·KOSPI/KOSDAQ)에 동일한 add_market_regime을 적용해
'시장 전체 레짐'을 따로 구하고, 종목 매수 신호에 AND 게이트로 결합한다.

  → 코인봇 auto_trader_v9의 'BTC 7단계 필터'를 주식 지수 레짐으로 번역한 것.
    BTC가 전체 시장을 게이팅하듯, 주식은 지수가 그 역할을 한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from stock_auto.config.settings import Market, get_config
from stock_auto.indicators.indicators_v2 import calculate_indicators
from stock_auto.strategy.regime_v2 import add_market_regime

REGIME_LABEL = {0: "약세(관망)", 1: "하락초기", 2: "보합", 3: "상승초기", 4: "강세"}


@dataclass
class MacroRegime:
    market: Market
    level: int                       # 종합 레짐(구성 지수 중 최소 = 보수적)
    per_index: dict[str, int] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return REGIME_LABEL.get(self.level, "?")

    @property
    def trade_allowed(self) -> bool:
        """레짐 0(약세)이면 전 종목 매수 보류 (AND 게이트 차단)."""
        return self.level >= 1


def compute_macro_regime(index_ohlcv: dict[str, pd.DataFrame],
                         market: Market) -> MacroRegime:
    """
    지수별 OHLCV → 지수 레짐 산출 → 보수적 종합(최소값).
    index_ohlcv: {index_symbol: 표준 OHLCV df}
    """
    cfg = get_config(market)
    per: dict[str, int] = {}
    for sym in cfg.index_symbols:
        df = index_ohlcv.get(sym)
        if df is None or len(df) < 60:
            continue
        # 지수도 종목과 동일 파이프라인으로 레짐 산출
        ind = calculate_indicators(df.copy(), market=market.value)
        if ind is None:
            continue
        ind = add_market_regime(ind)
        if "Regime" in ind.columns and len(ind) > 0:
            per[sym] = int(ind["Regime"].iloc[-1])
    level = min(per.values()) if per else 2   # 데이터 없으면 중립 가정
    return MacroRegime(market=market, level=level, per_index=per)


def macro_gate_ok(macro: MacroRegime) -> bool:
    """AND 게이트: 매크로 레짐 0이면 차단."""
    return macro.trade_allowed
