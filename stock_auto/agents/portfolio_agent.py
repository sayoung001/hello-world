"""
종합 판단 에이전트 (Stage 6) — 섹터+종목+뉴스+수급+밸류 종합 → 최종 추천.

출력: 설계 문서의 추천 스키마
  - 보유기간(단타/중단기/스윙), 진입 조건/구간, 손절, 목표
  - 당일 예상 손익 P10/P50/P90 (점추정 금지, 분포 범위)
  - 매매 방법(언제 매수/매도), 근거(섹터/시그널/뉴스/수급/밸류), 출처

복잡한 종합 추론이므로 적응형 사고(adaptive thinking) + Opus를 사용한다.
"""

from __future__ import annotations

from typing import Any

from stock_auto.agents.base import StockAgentBase

RECO_SCHEMA = {
    "type": "object",
    "properties": {
        "ticker": {"type": "string"},
        "recommend": {"type": "string", "enum": ["BUY", "WATCH", "AVOID"]},
        "horizon": {"type": "string", "enum": ["단타", "중단기", "스윙"]},
        "conviction": {"type": "number"},          # 0~1
        "entry_rule": {"type": "string"},           # 언제 매수(조건)
        "entry_zone": {"type": "array", "items": {"type": "number"}},
        "stop": {"type": "number"},
        "targets": {"type": "array", "items": {"type": "number"}},
        "expected_pnl_today": {
            "type": "object",
            "properties": {
                "p10_pct": {"type": "number"},
                "p50_pct": {"type": "number"},
                "p90_pct": {"type": "number"},
                "basis": {"type": "string"},
            },
            "required": ["p10_pct", "p50_pct", "p90_pct", "basis"],
            "additionalProperties": False,
        },
        "exit_plan": {"type": "string"},            # 언제 매도
        "rationale": {
            "type": "object",
            "properties": {
                "sector": {"type": "string"}, "signal": {"type": "string"},
                "news": {"type": "string"}, "flow": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["sector", "signal", "news", "flow", "value"],
            "additionalProperties": False,
        },
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ticker", "recommend", "horizon", "conviction", "entry_rule",
                 "entry_zone", "stop", "targets", "expected_pnl_today",
                 "exit_plan", "rationale", "sources"],
    "additionalProperties": False,
}

_SYSTEM = (
    "너는 한·미 주식 반자동 추천 시스템의 최종 판단 PM이다. "
    "규칙 기반 시그널, 섹터 상태, 그리고 뉴스/수급/밸류 에이전트의 코멘트를 종합해 "
    "실행 가능한 추천을 만든다. 규칙:\n"
    "1) 모든 진입가는 고정가가 아니라 '조건'으로 표현(예: 시가 -1% 눌림 & RVOL>1.5).\n"
    "2) 당일 예상 손익은 점추정 금지 — ATR/유사셋업 기반 P10/P50/P90 범위로.\n"
    "3) 제공된 근거에 없는 사실 날조 금지. 출처를 sources에 명시.\n"
    "4) 이것은 투자 권유가 아니라 분석 보조다. 과신 표현 자제.\n"
    "한국어로 작성한다."
)


class PortfolioAgent(StockAgentBase):
    def __init__(self, **kw):
        super().__init__(agent_id="portfolio", system_prompt=_SYSTEM, **kw)

    def decide(self, row: dict, news: dict, flow: dict, value: dict,
               sector_label: str = "-") -> dict:
        """
        row: screener.scored_all 한 행(dict)
        news/flow/value: 각 분석 에이전트 결과 dict
        """
        atr = row.get("atr")
        prompt = (
            f"[종목] {row.get('symbol')} ({row.get('market')})\n"
            f"[규칙 시그널] Effective {row.get('effective_score')}, "
            f"Money {row.get('money_score')}, Price {row.get('price_score')}, "
            f"Penalty {row.get('penalty')}, 전략 {row.get('strategies')}, "
            f"보유스타일 {row.get('styles')}\n"
            f"[레짐] 종목 {row.get('stock_regime')}, 매크로 {row.get('macro_regime')}, "
            f"전환 {row.get('regime_trans')}\n"
            f"[가격] 종가 {row.get('close')}, ATR {atr}, "
            f"목표 {row.get('target')}, 손절 {row.get('stop')}, R:R {row.get('rr_ratio')}\n"
            f"[섹터] {row.get('sector_etf')} 상태 '{sector_label}'(score {row.get('sector_status_score')})\n"
            f"[뉴스] {news.get('summary')} (sent {news.get('sentiment')})\n"
            f"[수급] {flow.get('summary')}\n"
            f"[밸류] {value.get('summary')}\n\n"
            "위를 종합해 추천 JSON을 작성하라. expected_pnl_today는 ATR과 R:R을 활용해 "
            "당일 변동 분포의 P10/P50/P90(%)로 제시하고 basis에 산출 근거를 적어라.")
        result = self.call_json(prompt, RECO_SCHEMA, max_tokens=2500, thinking=True)
        # 출처 보강: 뉴스 에이전트 sources 합류
        if isinstance(result, dict) and not result.get("_parse_error"):
            srcs = set(result.get("sources", [])) | set(news.get("sources", []))
            result["sources"] = sorted(s for s in srcs if s)
        return result
