"""
macro.py — Agent 2: 매크로 분석가
==================================
전통 금융시장 및 거시경제 환경 분석.
분석 유형: Subjective (주관적 해석)

데이터 소스:
- CoinGlass 공포탐욕지수
- LLM 웹 검색 기반 매크로 분석 (경제 일정, DXY, VIX)
- BTC-나스닥 상관관계 추정
"""

from __future__ import annotations
import ccxt
import pandas as pd
import numpy as np
from typing import Any

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage


class MacroAgent(AgentBase):
    """
    매크로 분석가

    책임:
    - 당일/익일 주요 경제 이벤트 파악
    - Risk-on vs Risk-off 환경 판단
    - DXY 방향성과 BTC 역상관 확인
    - 공포탐욕지수 기반 시장 과열/공포 판단
    """

    def __init__(self, cg_client=None):
        super().__init__(
            agent_id="agent_2_macro",
            agent_name="매크로 분석가",
            role_description="전통 금융시장 및 거시경제 환경 분석 (Subjective)"
        )
        self._analysis_type = "subjective"
        self.cg = cg_client

    def _get_system_prompt(self) -> str:
        return """당신은 크립토 트레이딩을 위한 거시경제 분석가입니다.
전통 금융시장(S&P500, DXY, VIX)이 크립토에 미치는 영향을 분석합니다.

분석 원칙:
- 20배 레버리지 트레이딩 환경에서의 리스크 평가
- 고임팩트 경제 이벤트(FOMC, CPI 등)는 최우선 고려
- 불확실하면 defensive 권고 (생존 우선)

반드시 JSON 형식으로 출력:
```json
{
  "risk_appetite": "risk-on|neutral|risk-off",
  "upcoming_events": [{"event": "이벤트명", "impact": "high|medium|low", "note": "요약"}],
  "dxy_trend": "bullish|bearish|neutral",
  "fear_greed": {"value": 0~100, "label": "라벨"},
  "equity_sentiment": "bullish|neutral|bearish",
  "recommendation": "aggressive|neutral|defensive",
  "risk_level": "SAFE|CAUTION|DANGER",
  "confidence": 0.0~1.0,
  "reasoning": "분석 근거"
}
```"""

    def collect_data(self) -> dict:
        """매크로 데이터 수집"""
        data = {}

        # 1. 공포탐욕지수 (CoinGlass)
        if self.cg:
            try:
                fgi = self.cg.get_fear_greed_index()
                data["fear_greed"] = fgi or {}
            except Exception:
                data["fear_greed"] = {}

        # 2. BTC 가격 변동 (매크로 이벤트 반응 대용)
        try:
            exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
            ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1d", limit=7)
            df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
            data["btc_daily"] = {
                "daily_changes": [
                    round((df["close"].iloc[i] - df["close"].iloc[i - 1]) / df["close"].iloc[i - 1] * 100, 2)
                    for i in range(1, len(df))
                ],
                "weekly_change": round(
                    (df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100, 2
                ),
                "avg_daily_range": round(
                    ((df["high"] - df["low"]) / df["close"] * 100).mean(), 2
                ),
            }
        except Exception:
            data["btc_daily"] = {}

        return data

    def analyze(self, collected_data: dict, context: dict | None = None) -> AgentMessage:
        """매크로 환경 분석"""
        fear_greed = collected_data.get("fear_greed", {})
        btc_daily = collected_data.get("btc_daily", {})

        prompt = f"""다음 데이터를 기반으로 크립토 시장의 매크로 환경을 분석하세요.

## 공포탐욕지수
{fear_greed if fear_greed else "데이터 없음"}

## BTC 일봉 데이터
- 일별 변동률: {btc_daily.get('daily_changes', 'N/A')}
- 주간 변동: {btc_daily.get('weekly_change', 'N/A')}%
- 평균 일일 레인지: {btc_daily.get('avg_daily_range', 'N/A')}%

## 현재 날짜 컨텍스트
오늘 날짜를 고려하여 최근 알려진 주요 경제 이벤트(FOMC, CPI, 고용 등)가
예정되어 있는지 판단하고, Risk-on/off 환경을 평가하세요.

20배 레버리지 환경에서 고임팩트 이벤트 전후에는 defensive를 권고하세요."""

        result = self.llm_json(prompt, deep=True)

        if result.get("parse_error"):
            result = self._fallback_analysis(collected_data)

        warnings = []
        if result.get("risk_level") == "DANGER":
            warnings.append("⚠️ 매크로 환경 위험")
        if result.get("recommendation") == "defensive":
            warnings.append("🛡️ 매크로: defensive 모드 권고")

        # 고임팩트 이벤트 경고
        for evt in result.get("upcoming_events", []):
            if evt.get("impact") == "high":
                warnings.append(f"📅 {evt.get('event', '?')} — 고임팩트")

        return self._build_message(
            data=result,
            confidence=result.get("confidence", 0.5),
            reasoning=result.get("reasoning", ""),
            warnings=warnings
        )

    def _fallback_analysis(self, data: dict) -> dict:
        """규칙 기반 폴백"""
        fgi = data.get("fear_greed", {})
        btc_daily = data.get("btc_daily", {})

        # 공포탐욕지수 기반 판단
        fgi_value = None
        if isinstance(fgi, dict):
            fgi_value = fgi.get("value", fgi.get("now", {}).get("value"))
        elif isinstance(fgi, (int, float)):
            fgi_value = fgi

        if fgi_value is not None:
            fgi_value = float(fgi_value)
            if fgi_value < 25:
                risk_appetite = "risk-off"
                recommendation = "defensive"
            elif fgi_value > 75:
                risk_appetite = "risk-on"
                recommendation = "aggressive"
            else:
                risk_appetite = "neutral"
                recommendation = "neutral"
        else:
            risk_appetite = "neutral"
            recommendation = "neutral"

        return {
            "risk_appetite": risk_appetite,
            "upcoming_events": [],
            "dxy_trend": "neutral",
            "fear_greed": {"value": fgi_value, "label": "unknown"},
            "recommendation": recommendation,
            "risk_level": "CAUTION" if recommendation == "defensive" else "SAFE",
            "confidence": 0.4,
            "reasoning": "규칙 기반 폴백 (공포탐욕지수 기반)"
        }
