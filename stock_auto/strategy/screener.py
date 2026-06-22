"""
스크리너 (Stage 2) — 규칙 기반 전체 유니버스 스캔.

실제 US_KR_)CLAUD 파이프라인을 충실히 오케스트레이션한다:
  calculate_indicators(df, market)   ← ★ market 전달(통화버그 수정)
  → add_market_regime (종목 레짐)
  → detect_regime_transition
  → apply_all_strategies(df, regime) → Effective_Score / Final_Buy
  → check_exit_signals
  + 매크로 지수 레짐 AND 게이트 (분석 §3.7 보완)

사용자 정책: 전체 유니버스 스캔은 '토큰/비용 없는 규칙 기반'만. LLM 에이전트는
이후 상위 추천(≤TOP_N)에만 적용한다.
분석 §3.9 교훈: 조용한 실패 금지 → 실패 종목/사유를 집계한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from stock_auto.config.settings import Market, get_config, TOP_N_FOR_AGENTS
from stock_auto.indicators.indicators_v2 import (
    calculate_indicators, check_immediate_hurdle,
)
from stock_auto.strategy.strategies import (
    apply_all_strategies, check_exit_signals,
    get_active_strategies, calculate_strategy_levels,
)
from stock_auto.strategy.regime_v2 import (
    add_market_regime, detect_regime_transition,
)
from stock_auto.strategy.macro_regime import (
    MacroRegime, compute_macro_regime, macro_gate_ok,
)


@dataclass
class ScreenResult:
    candidates: pd.DataFrame                 # 매수 후보(Effective 내림차순, ≤top_n)
    scored_all: pd.DataFrame                 # 전 종목 요약(노션/디버그용)
    exits: pd.DataFrame                      # 매도 신호 종목
    macro: MacroRegime
    failures: dict[str, str] = field(default_factory=dict)


def _passes_prefilter(df: pd.DataFrame, market: Market) -> bool:
    """거래대금 하한 사전필터 — 토큰 없이 유니버스 축소."""
    cfg = get_config(market)
    if df is None or len(df) < 60:
        return False
    tv = (df["Close"] * df["Volume"]).rolling(5).mean().iloc[-1]
    return bool(tv >= cfg.turnover_floor)


def screen_universe(
    ohlcv_map: dict[str, pd.DataFrame],
    market: Market,
    index_ohlcv: Optional[dict[str, pd.DataFrame]] = None,
    top_n: int = TOP_N_FOR_AGENTS,
    stock_sector_etf: Optional[dict[str, str]] = None,
    sector_status: Optional[dict[str, int]] = None,
) -> ScreenResult:
    """
    ohlcv_map: {symbol: 표준 OHLCV df (DatetimeIndex)}
    index_ohlcv: {index_symbol: df} — 매크로 레짐용
    stock_sector_etf: {symbol: 섹터 ETF 티커} — sector_map.get_sector_etf 결과
    sector_status: {ETF 티커: status_score 0~5} — sector_analysis.load_sector_status 결과

    분석 §3.2 해결: 섹터 상태를 매수 판정에 실제 반영한다.
      - 섹터 status_score 0(하락위험) → 매수 차단
    """
    macro = compute_macro_regime(index_ohlcv or {}, market)
    gate_ok = macro_gate_ok(macro)
    stock_sector_etf = stock_sector_etf or {}
    sector_status = sector_status or {}

    rows: list[dict] = []
    exit_rows: list[dict] = []
    failures: dict[str, str] = {}

    for symbol, raw in ohlcv_map.items():
        try:
            if not _passes_prefilter(raw, market):
                failures[symbol] = "prefilter(거래대금/길이)"
                continue

            # ★ 통화버그 수정: market을 끝까지 전달
            df = calculate_indicators(raw.copy(), market=market.value)
            if df is None:
                failures[symbol] = "indicators=None(데이터부족)"
                continue

            df = add_market_regime(df)
            rinfo = detect_regime_transition(df)
            df = apply_all_strategies(df, regime=rinfo["current"])
            df = check_exit_signals(df)

            last = df.iloc[-1]

            # 섹터 상태 조회 (분석 §3.2)
            sec_etf = stock_sector_etf.get(symbol, "-")
            sec_score = sector_status.get(sec_etf)  # None이면 미상

            # 매크로 AND 게이트 + 섹터 게이트
            #   - 매크로 약세(레짐0) → 차단 (§3.7)
            #   - 섹터 하락위험(score 0) → 차단 (§3.2)
            sector_ok = (sec_score is None) or (sec_score >= 1)
            final_buy = bool(last.get("Final_Buy", False)) and gate_ok and sector_ok

            active = get_active_strategies(last)
            levels = calculate_strategy_levels(last, active)

            rows.append({
                "symbol": symbol,
                "market": market.value,
                "effective_score": float(last.get("Effective_Score", 0.0)),
                "money_score": float(last.get("Money_Score", 0.0)),
                "price_score": float(last.get("Price_Score", 0.0)),
                "technical_score": float(last.get("Technical_Score", 0.0)),
                "liquidity_score": float(last.get("liquidity_score", 0.0)),
                "penalty": float(last.get("Penalty", 0.0)),
                "warning_count": int(last.get("Warning_Count", 0)),
                "stock_regime": rinfo["current"],
                "regime_trans": rinfo["transition"],
                "macro_regime": macro.level,
                "macro_gate": gate_ok,
                "sector_etf": sec_etf,
                "sector_status_score": sec_score,
                "final_buy": final_buy,
                "close": float(last["Close"]),
                "rsi_14": float(last.get("rsi_14", 0.0)),
                "adx": float(last.get("adx", 0.0)),
                "target": levels.get("target"),
                "stop": levels.get("stop"),
                "rr_ratio": levels.get("rr_ratio"),
                "styles": ",".join(sorted({a["style"] for a in active})),
                "strategies": ",".join(a["name"] for a in active),
            })

            if bool(last.get("Exit_Signal", False)):
                exit_rows.append({
                    "symbol": symbol, "market": market.value,
                    "exit_score": int(last.get("Exit_Score", 0)),
                    "close": float(last["Close"]),
                })

        except Exception as e:  # noqa: BLE001 — 사유 집계(조용한 실패 금지)
            failures[symbol] = f"{type(e).__name__}: {e}"

    scored_all = pd.DataFrame(rows)
    exits = pd.DataFrame(exit_rows)
    if scored_all.empty:
        return ScreenResult(scored_all, scored_all, exits, macro, failures)

    scored_all = scored_all.sort_values("effective_score", ascending=False)
    candidates = (scored_all[scored_all["final_buy"]]
                  .head(top_n).reset_index(drop=True))
    return ScreenResult(candidates, scored_all, exits, macro, failures)
