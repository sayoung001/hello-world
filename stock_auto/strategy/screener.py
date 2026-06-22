"""
스크리너 (Stage 2) — 규칙 기반 전체 유니버스 스캔.

사용자 정책: 전체 유니버스 스캔은 '토큰/비용 없는 규칙 기반'만 수행하고,
이후 LLM 에이전트 분석은 상위 추천 종목(≤15)에만 적용한다.

파이프라인:
  유니버스 → (거래대금 사전필터) → 지표 → 전략 점수 → 매크로 레짐 AND 게이트
          → 매수 후보 → Effective_Score 내림차순 정렬 → 상위 N 반환

분석 §3.9 교훈: 조용한 실패 금지 → 실패 종목/사유를 집계해 반환한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from stock_auto.config.settings import Market, get_config
from stock_auto.indicators.indicators import calculate_indicators
from stock_auto.strategy.strategies import score_symbol, is_buy
from stock_auto.strategy.regime import (
    MacroRegime, stock_regime, regime_gate, compute_macro_regime,
)


@dataclass
class ScreenResult:
    candidates: pd.DataFrame                 # 매수 후보(정렬됨)
    scored_all: pd.DataFrame                 # 전 종목 점수(디버그/노션용)
    macro: MacroRegime
    failures: dict[str, str] = field(default_factory=dict)  # symbol → 사유

    @property
    def top(self) -> pd.DataFrame:
        return self.candidates


def _passes_prefilter(df: pd.DataFrame, market: Market) -> bool:
    """거래대금 하한 사전필터 — 토큰 없이 유니버스 축소."""
    cfg = get_config(market)
    if len(df) < 60:
        return False
    tv5 = df["Turnover"].rolling(5).mean().iloc[-1]
    return bool(tv5 >= cfg.turnover_floor)


def screen_universe(
    ohlcv_map: dict[str, pd.DataFrame],
    market: Market,
    index_data: Optional[dict[str, pd.DataFrame]] = None,
    sector_status_map: Optional[dict[str, int]] = None,
    top_n: int = 15,
) -> ScreenResult:
    """
    ohlcv_map: {symbol: 표준 OHLCV df}
    index_data: {index_symbol: df}  (매크로 레짐용)
    sector_status_map: {symbol: 섹터상태 0~4}  (분석 §3.2 섹터 반영)
    top_n: LLM 분석 대상 상위 N (사용자 정책: 15)
    """
    macro = compute_macro_regime(index_data or {}, market)
    sector_status_map = sector_status_map or {}

    rows: list[dict] = []
    failures: dict[str, str] = {}

    for symbol, raw in ohlcv_map.items():
        try:
            if not _passes_prefilter(raw, market):
                failures[symbol] = "prefilter(거래대금/길이)"
                continue
            df = calculate_indicators(raw, market)
            res = score_symbol(df)
            s_lvl = stock_regime(df)
            gate_ok = regime_gate(s_lvl, macro)
            sec = sector_status_map.get(symbol)
            buy = is_buy(res, sector_status=sec, index_regime_ok=gate_ok)
            rows.append({
                "symbol": symbol,
                "market": market.value,
                "effective_score": res["effective_score"],
                "money_score": res["money_score"],
                "price_score": res["price_score"],
                "technical_score": res["technical_score"],
                "liquidity_score": res["liquidity_score"],
                "penalty": res["penalty"],
                "stock_regime": s_lvl,
                "macro_regime": macro.level,
                "sector_status": sec,
                "regime_gate": gate_ok,
                "is_buy": buy,
                "close": res["close"],
                "atr": res["atr"],
                "rsi": res["rsi"],
                "adx": res["adx"],
                "vai": res["vai"],
            })
        except Exception as e:  # noqa: BLE001 — 사유 집계(조용한 실패 금지)
            failures[symbol] = f"{type(e).__name__}: {e}"

    scored_all = pd.DataFrame(rows)
    if scored_all.empty:
        return ScreenResult(scored_all, scored_all, macro, failures)

    scored_all = scored_all.sort_values("effective_score", ascending=False)
    candidates = (scored_all[scored_all["is_buy"]]
                  .head(top_n)
                  .reset_index(drop=True))
    return ScreenResult(candidates, scored_all, macro, failures)
