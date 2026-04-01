"""
macro.py — Agent 2: 매크로 분석가 (Phase 2 구현 예정)
=====================================================
전통 금융시장 및 거시경제 환경 분석.
분석 유형: Subjective (주관적 해석)

데이터 소스 (예정):
- Yahoo Finance / Investing.com (S&P500, 나스닥, DXY)
- FRED (금리, 국채 수익률)
- CME FedWatch (금리 전망)
- 경제 캘린더
"""

from __future__ import annotations
from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage


class MacroAgent(AgentBase):
    """매크로 분석가 — Phase 2에서 구현"""

    def __init__(self):
        super().__init__(
            agent_id="agent_2_macro",
            agent_name="매크로 분석가",
            role_description="전통 금융시장 및 거시경제 환경 분석 (Subjective)"
        )
        self._analysis_type = "subjective"

    def _get_system_prompt(self) -> str:
        return """당신은 거시경제 환경 분석가입니다.
전통 금융시장(S&P500, DXY, VIX)과 크립토의 연관성을 분석합니다.

반드시 JSON으로 출력:
```json
{
  "risk_appetite": "risk-on|neutral|risk-off",
  "upcoming_events": [{"event": "...", "impact": "high|medium|low"}],
  "dxy_trend": "bullish|bearish|neutral",
  "recommendation": "aggressive|neutral|defensive",
  "reasoning": "분석 근거"
}
```"""

    def collect_data(self) -> dict:
        # TODO: Phase 2에서 Yahoo Finance, FRED API 연동
        return {"status": "stub", "message": "Phase 2에서 구현 예정"}

    def analyze(self, collected_data: dict, context: dict | None = None) -> AgentMessage:
        return self._build_message(
            data={
                "risk_appetite": "neutral",
                "upcoming_events": [],
                "dxy_trend": "neutral",
                "recommendation": "neutral",
                "reasoning": "매크로 에이전트 미구현 — 중립 반환"
            },
            confidence=0.3,
            reasoning="Phase 2 구현 예정",
            warnings=["매크로 에이전트 미구현"]
        )
