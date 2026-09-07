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
    level: int                       # 종합 레짐(그룹 내 max → 그룹 간 min)
    per_index: dict[str, int] = field(default_factory=dict)
    per_group: dict[str, int] = field(default_factory=dict)

    @property
    def detail(self) -> str:
        """'US500 R3 · IXIC R2 · QQQ R3' 형태 — 어떤 지수가 게이트를 막았는지 보이게."""
        return " · ".join(f"{k} R{v}" for k, v in self.per_index.items()) or "지수 데이터 없음"

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

    # 그룹 내부는 max(상관 지수는 한 표), 그룹 사이는 min(서로 다른 시장이므로 보수적 AND).
    # 예: 나스닥종합(IXIC)과 QQQ는 사실상 같은 시장이라 둘을 각각 세면
    #     나스닥에만 두 표를 주게 되어 게이트가 근거 없이 빡빡해진다.
    groups = getattr(cfg, "index_groups", None) or tuple((s,) for s in cfg.index_symbols)
    per_group: dict[str, int] = {}
    for g in groups:
        vals = [per[s] for s in g if s in per]
        if vals:
            per_group["/".join(g)] = max(vals)
    level = min(per_group.values()) if per_group else 2   # 데이터 없으면 중립 가정
    return MacroRegime(market=market, level=level, per_index=per, per_group=per_group)


def macro_gate_ok(macro: MacroRegime) -> bool:
    """AND 게이트: 매크로 레짐 0이면 차단."""
    return macro.trade_allowed
