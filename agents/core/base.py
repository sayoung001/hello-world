"""
base.py — AgentBase 클래스
===========================
모든 분석 에이전트의 베이스 클래스.
Quick LLM (Haiku) → 데이터 수집, Deep LLM (Sonnet) → 분석/추론 분리.
[Phase E] 비용 추적 + 프롬프트 캐싱 + 토큰 사용량 집계.
"""

from __future__ import annotations
import os
import json
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

try:
    import anthropic
except ImportError:
    anthropic = None

from agents.core.protocol import AgentMessage, RiskLevel


QUICK_MODEL = "claude-haiku-4-5-20251001"
DEEP_MODEL = "claude-sonnet-4-6"

# 토큰당 비용 (USD, 2026-04 기준 추정)
_COST_PER_1K = {
    QUICK_MODEL: {"input": 0.0008, "output": 0.004},
    DEEP_MODEL: {"input": 0.003, "output": 0.015},
}

# 글로벌 비용 추적기 (세션 누적)
_usage_stats = {
    "calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "estimated_cost_usd": 0.0,
    "errors": 0,
    "by_model": defaultdict(lambda: {"calls": 0, "input": 0, "output": 0, "cost": 0.0}),
    "by_agent": defaultdict(lambda: {"calls": 0, "cost": 0.0}),
}


def get_llm_usage() -> dict:
    """글로벌 LLM 사용량 조회"""
    return {
        "calls": _usage_stats["calls"],
        "input_tokens": _usage_stats["input_tokens"],
        "output_tokens": _usage_stats["output_tokens"],
        "cache_read_tokens": _usage_stats["cache_read_tokens"],
        "estimated_cost_usd": round(_usage_stats["estimated_cost_usd"], 6),
        "errors": _usage_stats["errors"],
        "by_model": dict(_usage_stats["by_model"]),
        "by_agent": dict(_usage_stats["by_agent"]),
    }


def reset_llm_usage():
    """글로벌 LLM 사용량 초기화"""
    _usage_stats["calls"] = 0
    _usage_stats["input_tokens"] = 0
    _usage_stats["output_tokens"] = 0
    _usage_stats["cache_read_tokens"] = 0
    _usage_stats["cache_write_tokens"] = 0
    _usage_stats["estimated_cost_usd"] = 0.0
    _usage_stats["errors"] = 0
    _usage_stats["by_model"].clear()
    _usage_stats["by_agent"].clear()


def _track_usage(model: str, agent_id: str, response):
    """API 응답에서 토큰 사용량 추출 + 비용 계산"""
    usage = getattr(response, "usage", None)
    if not usage:
        return

    inp = getattr(usage, "input_tokens", 0)
    out = getattr(usage, "output_tokens", 0)
    cache_read = getattr(usage, "cache_read_input_tokens", 0)
    cache_write = getattr(usage, "cache_creation_input_tokens", 0)

    costs = _COST_PER_1K.get(model, {"input": 0.003, "output": 0.015})
    # cache_write는 inp에 포함됨 → 중복 차감 후 개별 요율 적용
    effective_input = (inp - cache_read - cache_write) * 1.0 + cache_read * 0.1 + cache_write * 1.25
    cost = (effective_input * costs["input"] + out * costs["output"]) / 1000

    _usage_stats["calls"] += 1
    _usage_stats["input_tokens"] += inp
    _usage_stats["output_tokens"] += out
    _usage_stats["cache_read_tokens"] += cache_read
    _usage_stats["cache_write_tokens"] += cache_write
    _usage_stats["estimated_cost_usd"] += cost

    m = _usage_stats["by_model"][model]
    m["calls"] += 1
    m["input"] += inp
    m["output"] += out
    m["cost"] += cost

    a = _usage_stats["by_agent"][agent_id]
    a["calls"] += 1
    a["cost"] += cost


class AgentBase(ABC):
    """
    에이전트 베이스 클래스.

    [Phase E] 추가 기능:
    - 프롬프트 캐싱 (시스템 프롬프트를 cache_control로 마킹)
    - 토큰 사용량 + 비용 추적 (글로벌 싱글턴)
    - 호출 실패 시 에러 카운트
    """

    def __init__(self, agent_id: str, agent_name: str, role_description: str):
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.role_description = role_description
        self._client = None
        self._analysis_type: str = "factual"

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
                 max_tokens: int = 4096, cache_system: bool = True) -> str:
        """
        LLM 호출 (Quick/Deep 자동 분리)

        :param prompt: 사용자 프롬프트
        :param system: 시스템 프롬프트 (에이전트 역할 설정)
        :param deep: True면 Deep 모델(Sonnet), False면 Quick 모델(Haiku)
        :param max_tokens: 최대 토큰 수
        :param cache_system: True면 시스템 프롬프트에 cache_control 적용
        """
        model = DEEP_MODEL if deep else QUICK_MODEL
        system_prompt = system or self._get_system_prompt()

        # 프롬프트 캐싱: 시스템 프롬프트를 구조화된 형태로 전달
        if cache_system and system_prompt:
            system_content = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_content = system_prompt

        last_err = None
        for attempt in range(4):
            try:
                response = self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_content,
                    messages=[{"role": "user", "content": prompt}],
                )
                _track_usage(model, self.agent_id, response)
                return response.content[0].text
            except Exception as e:
                last_err = e
                err_str = str(e)
                if "529" in err_str or "overloaded" in err_str.lower():
                    wait = 2 ** (attempt + 1)
                    time.sleep(wait)
                    continue
                _usage_stats["errors"] += 1
                raise
        _usage_stats["errors"] += 1
        raise last_err

    def llm_json(self, prompt: str, system: str = "", deep: bool = False,
                 max_tokens: int = 4096, cache_system: bool = True) -> dict:
        """LLM 호출 후 JSON 파싱"""
        raw = self.llm_call(prompt, system, deep, max_tokens,
                           cache_system=cache_system)
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
        """데이터 수집 (Quick LLM 또는 API 호출)"""
        ...

    @abstractmethod
    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
        """분석 수행 (Deep LLM 사용)"""
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
