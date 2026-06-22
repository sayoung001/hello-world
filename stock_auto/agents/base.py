"""
에이전트 베이스 — Claude API 호출 공통 (Stage 4).

기존 코인봇 agents/core/base.py 패턴 계승 + 주식용으로 정리.

설계 원칙:
- 티어드: 전체 유니버스 스캔은 무토큰(규칙) → LLM은 추천 상위 N(≤15)에만.
- 근거 주입: 수치/원문을 프롬프트에 넣어 환각 억제, 출처 명시 강제.
- 모델: 기본 claude-opus-4-8 (15종목이라 비용 적음). 필요 시 STOCK_AGENT_MODEL로 하향.

네트워크/키 없는 환경 검증용으로 client를 주입할 수 있다(테스트의 FakeClient).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

try:
    import anthropic
except ImportError:  # 지연 — 실제 호출 시점에만 필요
    anthropic = None

# 모델 (사용자가 모델을 명시하지 않음 → 최신 Opus 기본. 비용 절감 원하면 env로 하향)
DEFAULT_MODEL = os.environ.get("STOCK_AGENT_MODEL", "claude-opus-4-8")


class StockAgentBase:
    """모든 주식 분석 에이전트의 베이스."""

    def __init__(self, agent_id: str, system_prompt: str,
                 model: str = DEFAULT_MODEL, client: Any = None):
        self.agent_id = agent_id
        self.system_prompt = system_prompt
        self.model = model
        self._client = client  # 주입 가능(테스트)

    @property
    def client(self):
        if self._client is None:
            if anthropic is None:
                raise ImportError("anthropic 패키지 필요: pip install anthropic")
            self._client = anthropic.Anthropic(
                api_key=os.environ.get("ANTHROPIC_API_KEY", "")
            )
        return self._client

    # ── 텍스트 호출 ──
    def call_text(self, prompt: str, max_tokens: int = 1200) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in resp.content if b.type == "text")

    # ── 구조화(JSON) 호출 — output_config.format으로 파싱 보장 ──
    def call_json(self, prompt: str, schema: dict,
                  max_tokens: int = 2000, thinking: bool = False) -> dict:
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            system=self.system_prompt,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if thinking:
            # 종합 판단처럼 복잡한 추론엔 적응형 사고 사용
            kwargs["thinking"] = {"type": "adaptive"}
        resp = self.client.messages.create(**kwargs)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return _safe_json(text)


def _safe_json(text: str) -> dict:
    """모델 출력에서 JSON 추출(코드펜스/잡텍스트 방어)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {"_parse_error": True, "_raw": text[:500]}
