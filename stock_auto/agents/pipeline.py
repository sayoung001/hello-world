"""
에이전트 파이프라인 (Stage 4~6) — 추천 상위 N 종목에만 LLM 적용.

흐름:
  screener 후보(top-N) → (US) saveticker 뉴스 수집
    → NewsAgent / FlowAgent / ValueAgent (종목별)
    → PortfolioAgent 종합 → 추천 리스트

사용자 정책: 전체 유니버스는 무토큰 규칙 스캔, LLM은 여기(추천 종목)만.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd

from stock_auto.config.settings import Market
from stock_auto.agents.base import DEFAULT_MODEL
from stock_auto.agents.analysts import NewsAgent, FlowAgent, ValueAgent
from stock_auto.agents.portfolio_agent import PortfolioAgent
from stock_auto.agents.news_collector import collect_for_tickers, NewsItem


@dataclass
class AgentPipelineResult:
    recommendations: list[dict] = field(default_factory=list)
    per_ticker: dict[str, dict] = field(default_factory=dict)  # 디버그(뉴스/수급/밸류)


def run_agents(
    candidates: pd.DataFrame,
    market: Market,
    client: Any = None,
    sector_label_map: Optional[dict[str, str]] = None,
    news_html: Optional[str] = None,
    top_n: int = 15,
) -> AgentPipelineResult:
    """
    candidates: screener.ScreenResult.candidates (이미 Effective순·≤top_n)
    client: anthropic 클라이언트(주입 가능; None이면 기본 생성). 테스트는 FakeClient.
    sector_label_map: {symbol: 섹터 상태 라벨}
    news_html: 미국 뉴스 HTML(없으면 라이브 수집). KR은 미수집(별도 소스 필요).
    """
    sector_label_map = sector_label_map or {}
    rows = candidates.head(top_n).to_dict("records")
    if not rows:
        return AgentPipelineResult()

    # 에이전트 인스턴스(클라이언트 공유)
    news_a = NewsAgent(client=client)
    flow_a = FlowAgent(client=client)
    value_a = ValueAgent(client=client)
    pm = PortfolioAgent(client=client)

    # 뉴스 수집(미국만)
    tickers = [r["symbol"] for r in rows]
    news_map: dict[str, list[NewsItem]] = (
        collect_for_tickers(tickers, html_text=news_html)
        if market == Market.US else {t: [] for t in tickers}
    )

    out = AgentPipelineResult()
    for r in rows:
        sym = r["symbol"]
        n = news_a.analyze(sym, news_map.get(sym, []))
        f = flow_a.analyze(sym, r)
        v = value_a.analyze(sym, r)
        label = sector_label_map.get(sym, "-")
        reco = pm.decide(r, n, f, v, sector_label=label)
        out.per_ticker[sym] = {"news": n, "flow": f, "value": v}
        out.recommendations.append(reco)
    return out


# ──────────────────────────────────────────────────────────────────────────
# 오프라인 검증용 FakeClient (API 키/네트워크 없이 파이프라인 로직 테스트)
# ──────────────────────────────────────────────────────────────────────────
class _Block:
    type = "text"
    def __init__(self, text): self.text = text


class _Resp:
    def __init__(self, text): self.content = [_Block(text)]


class FakeMessages:
    def create(self, **kwargs):
        import json
        # output_config.format이 있으면 스키마에 맞는 더미 JSON 반환
        fmt = kwargs.get("output_config", {}).get("format", {})
        schema = fmt.get("schema", {})
        return _Resp(json.dumps(_dummy_from_schema(schema)))


class FakeClient:
    """messages.create를 흉내내어 스키마 기반 더미 JSON을 돌려준다."""
    def __init__(self): self.messages = FakeMessages()


def _dummy_from_schema(schema: dict) -> Any:
    t = schema.get("type")
    if t == "object":
        return {k: _dummy_from_schema(v)
                for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return [_dummy_from_schema(schema.get("items", {"type": "string"}))]
    if t == "number":
        return 0.0
    if t == "string":
        enum = schema.get("enum")
        return enum[0] if enum else "테스트값"
    return None
