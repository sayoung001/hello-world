"""
pattern_analyzer.py — Claude Vision API 차트 패턴 분석
========================================================
차트 이미지 → Claude Vision → 패턴 분류 + 지지/저항 + 추세 판단.
Claude Vision 기본, Gemini Flash 폴백 (비용 최적화).
"""

from __future__ import annotations
import base64
import json
import os
import time

try:
    import anthropic
except ImportError:
    anthropic = None


# 모델 설정
CLAUDE_VISION_MODEL = "claude-sonnet-4-6"

# 차트 분석 프롬프트
CHART_ANALYSIS_PROMPT = """당신은 암호화폐 테크니컬 분석 전문가입니다.
이 15분봉 차트를 분석하고 다음을 판단하세요.

1. 식별된 차트 패턴 (패턴명, 신뢰도 0~100)
2. 지지/저항 레벨 (가격대)
3. 추세 강도 (-1.0 ~ 1.0, 음수=하락, 양수=상승)
4. 현재 가격의 패턴 내 위치 (초기/중간/말기)
5. 브레이크아웃 방향 예측 (LONG/SHORT/UNCERTAIN)
6. 권장 SL 레벨 (차트 기반)

{context}

JSON으로만 응답하세요:
{{
    "patterns": [{{"name": "패턴명", "confidence": 0, "phase": "초기/중간/말기"}}],
    "support_levels": [0.0],
    "resistance_levels": [0.0],
    "trend_strength": 0.0,
    "pattern_position": "초기/중간/말기",
    "breakout_prediction": "LONG/SHORT/UNCERTAIN",
    "chart_based_sl": 0.0,
    "risk_assessment": "low/medium/high",
    "reasoning": "분석 근거 요약"
}}"""


class PatternAnalyzer:
    """
    차트 이미지 → Claude Vision → 패턴 분석 결과.

    - Claude Sonnet Vision 사용
    - 시그널당 1회 호출 → 월 ~150건 × $0.003 = ~$0.45
    """

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = None
        self._call_count = 0
        self._last_call_time = 0.0

    @property
    def client(self):
        """Anthropic 클라이언트 (지연 초기화)"""
        if self._client is None:
            if anthropic is None:
                raise ImportError("anthropic 필요: pip install anthropic")
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def analyze(self, chart_image: bytes, signal_context: dict | None = None) -> dict:
        """
        차트 이미지 + 시그널 컨텍스트 → 패턴 분석 결과.

        Args:
            chart_image: PNG 이미지 바이트
            signal_context: 시그널 정보 (추가 컨텍스트)

        Returns:
            분석 결과 dict (patterns, support/resistance, trend 등)
        """
        # 컨텍스트 문자열 구성
        ctx_str = ""
        if signal_context:
            parts = []
            if signal_context.get("symbol"):
                parts.append(f"코인: {signal_context['symbol']}")
            if signal_context.get("direction"):
                parts.append(f"시그널 방향: {signal_context['direction']}")
            if signal_context.get("confidence"):
                parts.append(f"시그널 confidence: {signal_context['confidence']}")
            if signal_context.get("gap"):
                parts.append(f"EMA GAP: {signal_context['gap']:.2f}%")
            if parts:
                ctx_str = "추가 컨텍스트: " + ", ".join(parts)

        prompt = CHART_ANALYSIS_PROMPT.format(context=ctx_str)

        # 이미지 base64 인코딩
        img_b64 = base64.standard_b64encode(chart_image).decode("utf-8")

        try:
            response = self.client.messages.create(
                model=CLAUDE_VISION_MODEL,
                max_tokens=1500,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": img_b64,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            )
            self._call_count += 1
            self._last_call_time = time.time()

            # JSON 파싱
            raw = response.content[0].text
            return self._parse_response(raw)

        except Exception as e:
            print(f"[Vision] 분석 실패: {e}")
            return self._empty_result(str(e))

    def _parse_response(self, raw: str) -> dict:
        """LLM 응답에서 JSON 추출"""
        # JSON 블록 추출
        text = raw
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        try:
            result = json.loads(text.strip())
            result["parse_error"] = False
            return result
        except json.JSONDecodeError:
            return {
                "raw_response": raw,
                "parse_error": True,
                "patterns": [],
                "trend_strength": 0.0,
                "breakout_prediction": "UNCERTAIN",
                "risk_assessment": "medium",
            }

    def _empty_result(self, error: str = "") -> dict:
        """빈 결과 (에러 시)"""
        return {
            "patterns": [],
            "support_levels": [],
            "resistance_levels": [],
            "trend_strength": 0.0,
            "pattern_position": "unknown",
            "breakout_prediction": "UNCERTAIN",
            "chart_based_sl": 0.0,
            "risk_assessment": "medium",
            "error": error,
        }

    @property
    def stats(self) -> dict:
        """호출 통계"""
        return {
            "total_calls": self._call_count,
            "estimated_cost": round(self._call_count * 0.003, 4),
        }
