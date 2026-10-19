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

# 재시도 — 일시 오류만. 종목 단위 격리(agents/pipeline.py)와 2단 방어다.
AGENT_RETRIES = int(os.environ.get("STOCK_AGENT_RETRIES", "2"))
AGENT_BACKOFF_SEC = float(os.environ.get("STOCK_AGENT_BACKOFF_SEC", "3"))


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
        resp = self._create(
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
        resp = self._create(**kwargs)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        out = _safe_json(text)
        if isinstance(out, dict) and not out.get("_parse_error"):
            out["_usage"] = usage_of(resp)
        return out

    # ── 재시도 ──
    def _create(self, **kwargs):
        """일시 오류(429·5xx·네트워크)만 재시도한다.

        SDK 기본 재시도는 2회지만 전부 소진되면 예외가 올라오고, 호출부가
        종목 단위로 막지 않으면 배치 전체가 죽는다. 여기서 한 겹 더 받되
        **400 같은 요청 오류는 재시도하지 않는다** — 프롬프트나 스키마가
        틀린 것이라 다시 보내도 같은 결과이고, 15종목 × 4회 × 재시도만큼
        시간과 돈을 버린다.
        """
        import time
        last: Optional[BaseException] = None
        for attempt in range(AGENT_RETRIES + 1):
            try:
                return self.client.messages.create(**kwargs)
            except Exception as e:  # noqa: BLE001 — 아래에서 분류해 재던진다
                last = e
                if not _is_transient(e) or attempt == AGENT_RETRIES:
                    raise
                delay = AGENT_BACKOFF_SEC * (2 ** attempt)
                print(f"[agent:{self.agent_id}] {type(e).__name__} — "
                      f"{delay}초 후 재시도 ({attempt + 1}/{AGENT_RETRIES})")
                time.sleep(delay)
        raise last  # 도달하지 않음(루프에서 raise)


def _is_transient(e: BaseException) -> bool:
    """재시도해서 의미가 있는 오류인가."""
    name = type(e).__name__
    if name in ("RateLimitError", "APIConnectionError", "APITimeoutError",
                "InternalServerError", "APIConnectionTimeoutError"):
        return True
    code = getattr(e, "status_code", None)
    if isinstance(code, int):
        return code == 429 or code >= 500
    return False


def usage_of(resp: Any) -> dict:
    """응답의 토큰 사용량. 지금까지 한 번도 읽지 않아 실제 지출과
    프롬프트 변경의 토큰 영향을 알 수 없었다."""
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    return {k: v for k in ("input_tokens", "output_tokens",
                           "cache_read_input_tokens",
                           "cache_creation_input_tokens")
            if (v := getattr(u, k, None)) is not None}


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
