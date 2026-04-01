"""
correlation.py — Agent 3: 상관관계 분석가 (Phase 2 구현 예정)
=============================================================
BTC와 타 자산 간 연동성 분석.
분석 유형: Factual (사실 기반)
"""

from __future__ import annotations
from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage


class CorrelationAgent(AgentBase):
    """상관관계 분석가 — Phase 2에서 구현"""

    def __init__(self):
        super().__init__(
            agent_id="agent_3_corr",
            agent_name="상관관계 분석가",
            role_description="BTC와 타 자산 간 연동성 분석 (Factual)"
        )
        self._analysis_type = "factual"

    def _get_system_prompt(self) -> str:
        return """당신은 자산 간 상관관계 전문 분석가입니다.
BTC가 현재 '디지털 금'으로 움직이는지 '테크 주식'으로 움직이는지 판별합니다."""

    def collect_data(self) -> dict:
        return {"status": "stub"}

    def analyze(self, collected_data: dict, context: dict | None = None) -> AgentMessage:
        return self._build_message(
            data={
                "btc_behaves_as": "independent",
                "correlation_stability": "unknown",
                "rebalancing_risk": False,
                "reasoning": "상관관계 에이전트 미구현 — 독립 가정"
            },
            confidence=0.3,
            reasoning="Phase 2 구현 예정",
            warnings=["상관관계 에이전트 미구현"]
        )
