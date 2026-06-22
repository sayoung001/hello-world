"""
분석 에이전트 3종 (Stage 4) — 뉴스 / 수급 / 밸류.

각 에이전트는 '근거 데이터를 입력으로 주입'받아 환각을 억제하고 출처를 명시한다.
추천 상위 N(≤15) 종목에만 호출(사용자 정책).
"""

from __future__ import annotations

from typing import Any, Optional

from stock_auto.agents.base import StockAgentBase
from stock_auto.agents.news_collector import NewsItem

# ── 공통 출력 스키마 조각 ──
_SENTIMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "sentiment": {"type": "number"},          # -1 ~ +1
        "key_points": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "sentiment", "key_points", "sources"],
    "additionalProperties": False,
}


class NewsAgent(StockAgentBase):
    """뉴스 요약 + 센티먼트. saveticker 헤드라인 원문 주입."""
    def __init__(self, **kw):
        super().__init__(
            agent_id="news",
            system_prompt=(
                "너는 주식 뉴스 분석가다. 제공된 헤드라인'만'을 근거로 분석한다. "
                "헤드라인에 없는 사실을 지어내지 마라. 출처(URL/매체)를 sources에 명시하라. "
                "한국어로 간결히 작성한다."),
            **kw)

    def analyze(self, ticker: str, news: list[NewsItem]) -> dict:
        if not news:
            return {"summary": "수집된 뉴스 없음", "sentiment": 0.0,
                    "key_points": [], "sources": []}
        lines = "\n".join(f"- {n.headline} ({n.url})" for n in news[:15])
        prompt = (
            f"[종목] {ticker}\n[헤드라인]\n{lines}\n\n"
            "위 헤드라인만으로 핵심 이슈를 3줄 이내로 요약하고, "
            "센티먼트를 -1(매우부정)~+1(매우긍정)로 점수화하라.")
        return self.call_json(prompt, _SENTIMENT_SCHEMA, max_tokens=900)


class FlowAgent(StockAgentBase):
    """수급/거래 해석 — 거래대금·RVOL·VAI·체결 등 수치 주입."""
    def __init__(self, **kw):
        super().__init__(
            agent_id="flow",
            system_prompt=(
                "너는 수급 분석가다. 제공된 수치만으로 자금 흐름(매집/분산/단발)을 해석한다. "
                "수치에 근거 없는 단정은 금지. 한국어로 간결히."),
            **kw)

    def analyze(self, ticker: str, metrics: dict) -> dict:
        prompt = (
            f"[종목] {ticker}\n[수급 수치]\n"
            f"- Effective {metrics.get('effective_score')}, Money {metrics.get('money_score')}\n"
            f"- RVOL {metrics.get('rvol')}, VAI {metrics.get('vai_stage1')}/{metrics.get('vai_stage2')}\n"
            f"- 유동성 {metrics.get('liquidity_score')}, 거래대금 {metrics.get('trading_value')}\n"
            f"- 종목레짐 {metrics.get('stock_regime')}, 매크로 {metrics.get('macro_regime')}\n\n"
            "자금이 실제로 들어오는지(매집/분산/단발 펌핑) 한줄 판단 + 근거.")
        return self.call_json(prompt, _SENTIMENT_SCHEMA, max_tokens=700)


class ValueAgent(StockAgentBase):
    """가격/밸류·기술적 위치 평가."""
    def __init__(self, **kw):
        super().__init__(
            agent_id="value",
            system_prompt=(
                "너는 밸류/기술 분석가다. 제공된 가격·지표 수치만으로 과열/저평가와 "
                "기술적 위치(지지/저항)를 평가한다. 한국어로 간결히."),
            **kw)

    def analyze(self, ticker: str, metrics: dict) -> dict:
        prompt = (
            f"[종목] {ticker}\n[가격/기술 수치]\n"
            f"- 종가 {metrics.get('close')}, RSI {metrics.get('rsi_14')}, ADX {metrics.get('adx')}\n"
            f"- ATR {metrics.get('atr')}, 목표 {metrics.get('target')}, 손절 {metrics.get('stop')}, "
            f"R:R {metrics.get('rr_ratio')}\n"
            f"- 섹터상태 {metrics.get('sector_status_score')}\n\n"
            "현재 가격대가 과열/적정/저평가 중 무엇인지 + 기술적 위치 한줄.")
        return self.call_json(prompt, _SENTIMENT_SCHEMA, max_tokens=700)
