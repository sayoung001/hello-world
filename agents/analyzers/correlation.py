"""
correlation.py — Agent 3: 상관관계 분석가
==========================================
BTC와 타 자산 간 연동성 분석.
분석 유형: Factual (사실 기반)

데이터 소스:
- Binance (BTC, ETH 가격 → BTC/ETH 비율 추이)
- 내부 계산: Rolling 상관계수, Regime 변화 감지
"""

from __future__ import annotations
import ccxt
import pandas as pd
import numpy as np
from typing import Any

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage


class CorrelationAgent(AgentBase):
    """
    상관관계 분석가

    책임:
    - BTC vs ETH 상관계수 및 비율 변화
    - 알트코인 동조화 vs 분산 판단
    - BTC 도미넌스 추이
    - Regime 변화 감지 (동조→분산 전환 = 불안정)
    """

    def __init__(self, exchange=None):
        super().__init__(
            agent_id="agent_3_corr",
            agent_name="상관관계 분석가",
            role_description="BTC와 타 자산 간 연동성 분석 (Factual)"
        )
        self._analysis_type = "factual"
        self.exchange = exchange or ccxt.binance({
            "enableRateLimit": True,
            "options": {"defaultType": "future"}
        })

    def _get_system_prompt(self) -> str:
        return """당신은 자산 간 상관관계 전문 분석가입니다.
크립토 자산들 간의 연동성을 분석하여 시장 안정성을 판단합니다.

분석 원칙:
- 높은 상관관계 → 시장 동조화 (방향성 명확)
- 낮은 상관관계 → 분산 (불확실성 증가)
- 상관관계 급변(breakdown) → 레짐 전환 신호
- 20배 레버리지에서 불확실성 = 위험

반드시 JSON 형식으로 출력:
```json
{
  "btc_eth_correlation": 상관계수(-1~1),
  "btc_eth_ratio_trend": "rising|stable|falling",
  "alt_synchronization": 0.0~1.0(동조도),
  "btc_behaves_as": "gold|tech_stock|independent",
  "correlation_stability": "stable|shifting|breakdown",
  "rebalancing_risk": true|false,
  "risk_level": "SAFE|CAUTION|DANGER",
  "confidence": 0.0~1.0,
  "reasoning": "분석 근거"
}
```"""

    def collect_data(self) -> dict:
        """상관관계 데이터 수집"""
        data = {}
        pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]

        for pair in pairs:
            try:
                ohlcv = self.exchange.fetch_ohlcv(pair, "1h", limit=168)  # 7일
                df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
                data[pair.replace("/USDT", "")] = df["close"].values.tolist()
            except Exception:
                pass

        return data

    def analyze(self, collected_data: dict, context: dict | None = None) -> AgentMessage:
        """상관관계 분석 수행"""
        btc = collected_data.get("BTC", [])
        eth = collected_data.get("ETH", [])
        sol = collected_data.get("SOL", [])
        bnb = collected_data.get("BNB", [])

        result = self._compute_correlations(btc, eth, sol, bnb)

        # LLM 보강 분석 (데이터가 있을 때만)
        if btc and eth:
            try:
                prompt = f"""다음 상관관계 데이터를 분석하세요.

## BTC-ETH 상관계수
- 7일 전체: {result.get('btc_eth_corr_7d', 'N/A'):.3f}
- 최근 24시간: {result.get('btc_eth_corr_24h', 'N/A'):.3f}
- 변화: {result.get('corr_change', 'N/A')}

## BTC-ETH 비율
- 현재: {result.get('btc_eth_ratio', 'N/A'):.2f}
- 7일 전: {result.get('btc_eth_ratio_7d_ago', 'N/A'):.2f}

## 알트 동조도
- BTC-SOL: {result.get('btc_sol_corr', 'N/A'):.3f}
- BTC-BNB: {result.get('btc_bnb_corr', 'N/A'):.3f}
- 평균 동조도: {result.get('avg_sync', 'N/A'):.3f}

20배 레버리지 환경에서 상관관계 변화가 의미하는 바를 분석하세요."""

                llm_result = self.llm_json(prompt, deep=True)
                if not llm_result.get("parse_error"):
                    result.update(llm_result)
            except Exception:
                pass

        warnings = []
        if result.get("correlation_stability") == "breakdown":
            warnings.append("⚠️ 상관관계 붕괴 감지 — 레짐 전환 주의")
        if result.get("rebalancing_risk"):
            warnings.append("📊 월말/분기말 리밸런싱 주의")

        return self._build_message(
            data=result,
            confidence=result.get("confidence", 0.5),
            reasoning=result.get("reasoning", ""),
            warnings=warnings
        )

    def _compute_correlations(self, btc: list, eth: list, sol: list, bnb: list) -> dict:
        """상관계수 계산"""
        result = {
            "btc_eth_corr_7d": 0.0,
            "btc_eth_corr_24h": 0.0,
            "corr_change": "unknown",
            "btc_eth_ratio": 0.0,
            "btc_eth_ratio_7d_ago": 0.0,
            "btc_sol_corr": 0.0,
            "btc_bnb_corr": 0.0,
            "avg_sync": 0.5,
            "correlation_stability": "unknown",
            "rebalancing_risk": False,
            "risk_level": "CAUTION",
            "confidence": 0.4,
            "reasoning": "데이터 기반 상관관계 계산",
        }

        if not btc or not eth or len(btc) < 48:
            result["reasoning"] = "데이터 불충분"
            return result

        min_len = min(len(btc), len(eth))
        btc_arr = np.array(btc[-min_len:])
        eth_arr = np.array(eth[-min_len:])

        # 수익률 기반 상관계수
        btc_ret = np.diff(btc_arr) / btc_arr[:-1]
        eth_ret = np.diff(eth_arr) / eth_arr[:-1]

        if len(btc_ret) >= 48:
            result["btc_eth_corr_7d"] = float(np.corrcoef(btc_ret, eth_ret)[0, 1])
            result["btc_eth_corr_24h"] = float(np.corrcoef(btc_ret[-24:], eth_ret[-24:])[0, 1])

            corr_diff = result["btc_eth_corr_24h"] - result["btc_eth_corr_7d"]
            if corr_diff < -0.2:
                result["corr_change"] = "decorrelating"
                result["correlation_stability"] = "shifting"
            elif corr_diff > 0.2:
                result["corr_change"] = "converging"
                result["correlation_stability"] = "stable"
            else:
                result["corr_change"] = "stable"
                result["correlation_stability"] = "stable"

        # BTC/ETH 비율
        if eth_arr[-1] > 0 and eth_arr[0] > 0:
            result["btc_eth_ratio"] = float(btc_arr[-1] / eth_arr[-1])
            result["btc_eth_ratio_7d_ago"] = float(btc_arr[0] / eth_arr[0])

        # SOL, BNB 상관관계
        corrs = [result["btc_eth_corr_7d"]]
        if sol and len(sol) >= min_len:
            sol_ret = np.diff(np.array(sol[-min_len:])) / np.array(sol[-min_len:])[:-1]
            if len(sol_ret) == len(btc_ret):
                c = float(np.corrcoef(btc_ret, sol_ret)[0, 1])
                result["btc_sol_corr"] = c
                corrs.append(c)
        if bnb and len(bnb) >= min_len:
            bnb_ret = np.diff(np.array(bnb[-min_len:])) / np.array(bnb[-min_len:])[:-1]
            if len(bnb_ret) == len(btc_ret):
                c = float(np.corrcoef(btc_ret, bnb_ret)[0, 1])
                result["btc_bnb_corr"] = c
                corrs.append(c)

        result["avg_sync"] = float(np.mean(corrs)) if corrs else 0.5

        # 위험도 판단
        if result["avg_sync"] < 0.3 or result["correlation_stability"] == "breakdown":
            result["risk_level"] = "CAUTION"
        else:
            result["risk_level"] = "SAFE"

        result["confidence"] = 0.6
        return result
