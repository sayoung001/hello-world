"""
alt_ecosystem.py — Agent 4: 알트코인 생태계 분석가 (Phase 2 구현 예정)
=====================================================================
알트코인 시장 전체 동향, 분산도, 섹터 모멘텀 분석.
분석 유형: Subjective (주관적 해석)
"""

from __future__ import annotations
from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage


class AltEcosystemAgent(AgentBase):
    """알트코인 생태계 분석가 — Phase 2에서 구현"""

    def __init__(self):
        super().__init__(
            agent_id="agent_4_alt",
            agent_name="알트 생태계 분석가",
            role_description="알트코인 시장 동향 및 분산도 분석 (Subjective)"
        )
        self._analysis_type = "subjective"

    def _get_system_prompt(self) -> str:
        return """당신은 알트코인 생태계 전문 분석가입니다.
BTC 도미넌스, 섹터 로테이션, 분산도를 분석합니다."""

    def collect_data(self) -> dict:
        return {"status": "stub"}

    def analyze(self, collected_data: dict, context: dict | None = None) -> AgentMessage:
        return self._build_message(
            data={
                "dispersion_index": 0.5,
                "market_consensus": "unclear",
                "btc_dominance_trend": "unknown",
                "reasoning": "알트 생태계 에이전트 미구현"
            },
            confidence=0.3,
            reasoning="Phase 2 구현 예정",
            warnings=["알트 생태계 에이전트 미구현"]
        )
