"""
base.py — AgentBase 클래스
===========================
모든 분석 에이전트의 베이스 클래스.
Quick LLM (Haiku) → 데이터 수집, Deep LLM (Sonnet) → 분석/추론 분리.
"""

from __future__ import annotations
import os
import json
import time
from abc import ABC, abstractmethod
from typing import Any

try:
    import anthropic
except ImportError:
    anthropic = None  # 지연 초기화 — 실제 LLM 호출 시점에 확인

from agents.core.protocol import AgentMessage, RiskLevel


# LLM 모델 설정 (Quick/Deep 분리 — TradingAgents 차용)
QUICK_MODEL = "claude-haiku-4-5-20251001"    # 데이터 수집/요약용
DEEP_MODEL = "claude-sonnet-4-6"             # 분석/토론/추론용


class AgentBase(ABC):
    """
    에이전트 베이스 클래스

    모든 에이전트가 공통으로 가지는 기능:
    - LLM 호출 (Quick/Deep 분리)
    - 구조화된 출력 생성
    - 분석 결과를 AgentMessage로 래핑
    """

    def __init__(self, agent_id: str, agent_name: str, role_description: str):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.role_description = role_description
        self._client = None
        self._analysis_type: str = "factual"  # "factual" | "subjective" (FS-ReasoningAgent 차용)

    @property
    def client(self):
        """Anthropic 클라이언트 (지연 초기화)"""
        if self._client is None:
            if anthropic is None:
                raise ImportError("anthropic 패키지 필요: pip install anthropic")
            self._client = anthropic.Anthropic(
                api_key=os.environ.get("ANTHROPIC_API_KEY", "")
            )
        return self._client

    def llm_call(self, prompt: str, system: str = "", deep: bool = False,
                 max_tokens: int = 4096) -> str:
        """
        LLM 호출 (Quick/Deep 자동 분리)

        :param prompt: 사용자 프롬프트
        :param system: 시스템 프롬프트 (에이전트 역할 설정)
        :param deep: True면 Deep 모델(Sonnet) 사용, False면 Quick 모델(Haiku)
        :param max_tokens: 최대 토큰 수
        """
        model = DEEP_MODEL if deep else QUICK_MODEL
        system_prompt = system or self._get_system_prompt()

        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    def llm_json(self, prompt: str, system: str = "", deep: bool = False,
                 max_tokens: int = 4096) -> dict:
        """LLM 호출 후 JSON 파싱"""
        raw = self.llm_call(prompt, system, deep, max_tokens)
        # JSON 블록 추출
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0]
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0]
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            return {"raw_response": raw, "parse_error": True}

    @abstractmethod
    def _get_system_prompt(self) -> str:
        """에이전트별 시스템 프롬프트 반환"""
        ...

    @abstractmethod
    def collect_data(self) -> dict:
        """
        데이터 수집 (Quick LLM 또는 API 호출)
        각 에이전트가 필요한 데이터를 수집하여 dict로 반환
        """
        ...

    @abstractmethod
    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
        """
        분석 수행 (Deep LLM 사용)

        :param collected_data: collect_data()의 결과
        :param context: 다른 에이전트의 분석 결과 (토론 시)
        :param crash_context: 급락 감지 정보 (긴급 분석 시)
        """
        ...

    def run(self, context: dict | None = None,
            crash_context: dict | None = None) -> AgentMessage:
        """전체 파이프라인 실행: 데이터 수집 → 분석 → 메시지 생성"""
        data = self.collect_data()
        return self.analyze(data, context, crash_context=crash_context)

    def _build_message(self, data: dict, confidence: float,
                       reasoning: str = "", warnings: list[str] | None = None) -> AgentMessage:
        """분석 결과를 AgentMessage로 래핑"""
        return AgentMessage(
            agent_id=self.agent_id,
            agent_name=self.agent_name,
            confidence=confidence,
            data=data,
            reasoning=reasoning,
            warnings=warnings or []
        )
