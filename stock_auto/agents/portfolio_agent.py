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
        "entry_rule": {"type": "string"},           # 언제 매수(조건) — 사람이 읽는 문장
        # ★ 기계가 읽는 진입 트리거. entry_rule만으로는 가격 도달을 감시할 수 없다.
        #   (effective_score 사건의 교훈: 스키마에 없는 키는 LLM이 반환할 수 없다)
        "entry_trigger": {
            "type": "object",
            "properties": {
                # pullback  = 기준가 아래로 value% 눌리면 발화
                # breakout  = 기준가 위로 value% 돌파하면 발화
                # immediate = 조건 없이 다음 개장에 진입
                "kind": {"type": "string",
                         "enum": ["pullback", "breakout", "immediate"]},
                # 1.5 = 1.5%. immediate면 0.
                # **반드시 양수**. 방향은 kind 가 정한다 — 진입 문장이
                # '-0.5% 눌림'이어도 여기는 0.5 로 적어야 한다. 음수를 넣으면
                # 도달가가 기준가 위로 계산되어 조건이 정반대로 뒤집힌다.
                #
                # ★ 여기에 minimum/maximum 을 넣지 마라. 구조화 출력 API 는 number 의
                #   수치 제약을 지원하지 않아 **요청 전체가 400** 으로 거부된다.
                #   실측: 2026-10-20 배치에서 후보 4건이 전부 이 400 으로 탈락해
                #   추천 0건이 됐다. 범위 검증은 tracking.triggers.resolve_condition
                #   (abs + MAX_SANE_PCT) 이 맡는다 — 그쪽이 진짜 안전장치다.
                "value_pct": {"type": "number"},
                # 기준가: signal_close(추천일 종가) / prev_close / open(당일 시가)
                "basis": {"type": "string",
                          "enum": ["signal_close", "prev_close", "open"]},
                "split": {"type": "boolean"},        # 분할 진입 권고 여부
            },
            "required": ["kind", "value_pct", "basis", "split"],
            "additionalProperties": False,
        },
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
                 "entry_trigger", "entry_zone", "stop", "targets",
                 "expected_pnl_today", "exit_plan", "rationale", "sources"],
    "additionalProperties": False,
}

_SYSTEM = (
    "너는 한·미 주식 반자동 추천 시스템의 최종 판단 PM이다. "
    "규칙 기반 시그널, 섹터 상태, 그리고 뉴스/수급/밸류 에이전트의 코멘트를 종합해 "
    "실행 가능한 추천을 만든다. 규칙:\n"
    "1) 모든 진입가는 고정가가 아니라 '조건'으로 표현(예: 시가 -1% 눌림 & RVOL>1.5).\n"
    "   그리고 그 조건을 entry_trigger에 **기계가 읽을 수 있게** 똑같이 담아라.\n"
    "   예) '1.5% 눌림 시 분할 진입' → kind=pullback, value_pct=1.5,\n"
    "       basis=signal_close, split=true\n"
    "   ★ value_pct 는 **항상 양수**로 적어라. 방향은 kind 가 정한다.\n"
    "     '-0.5% 눌림' 이라고 썼더라도 value_pct=0.5 다. 음수를 넣으면\n"
    "     도달가가 기준가 위로 계산되어 조건이 정반대로 뒤집힌다.\n"
    "   예) '종가 돌파 확인 후 진입'  → kind=breakout, value_pct=0.5,\n"
    "       basis=signal_close, split=false\n"
    "   눌림/돌파 조건이 없으면 kind=immediate, value_pct=0.\n"
    "2) 당일 예상 손익은 점추정 금지 — ATR/유사셋업 기반 P10/P50/P90 범위로.\n"
    "3) 제공된 근거에 없는 사실 날조 금지. 출처를 sources에 명시.\n"
    "4) 이것은 투자 권유가 아니라 분석 보조다. 과신 표현 자제.\n"
    # ── conviction 의 뜻을 정의한다 ──
    # 예전에는 0~1 범위만 주고 무슨 뜻인지 말하지 않았다. 그러니 중간값에
    # 몰리고(관측: 0.52·0.55·0.68) 실현손익과의 관계도 생기지 않는다.
    # 확률은 '빈도'로 정의해야 보정이 가능하다.
    "5) conviction 은 **목표가에 손절보다 먼저 닿을 확률**이다. 빈도로 생각하라:\n"
    "   0.75 = 열 번 중 일곱~여덟 번   0.60 = 열 번 중 여섯 번\n"
    "   0.50 = 반반(모르겠다)          0.35 = 열 번 중 세~네 번\n"
    "   같은 날 추천끼리 값이 거의 같으면 변별력이 없다 — 종목별로 다르게 매겨라.\n"
    "6) [실측 기저율]이 주어지면 **그 숫자에서 출발해** 증거만큼 조정하라.\n"
    "   기저율이 없다고 하면 0.5 근처에서 출발하고, 근거 없이 올리지 마라.\n"
    "   0.9 이상이나 0.1 이하는 거의 쓰지 마라 — 이 전략의 실측 승률은\n"
    "   그런 극단을 뒷받침하지 않는다.\n"
    "한국어로 작성한다."
)


# 종합 판단의 출력 한도. 사고 토큰이 여기에 포함되고, effort="high" 면 사고가
# 길어진다. 예전 2500 은 opus-4-8 기본 effort(high) 기준이었고 opus-5-5 는
# 기본이 medium 이라 high 를 명시하는 만큼 여유가 더 필요하다.
PM_MAX_TOKENS = 8000


class PortfolioAgent(StockAgentBase):
    def __init__(self, **kw):
        # 기본 effort 가 opus-4-8 의 high 에서 medium 으로 내려갔다. 종합 판단은
        # 이 시스템에서 가장 어려운 추론이므로 high 를 명시해 예전 수준을 유지한다.
        kw.setdefault("effort", "high")
        super().__init__(agent_id="portfolio", system_prompt=_SYSTEM, **kw)

    def decide(self, row: dict, news: dict, flow: dict, value: dict,
               sector_label: str = "-", prior_text: str = "",
               market_news: str = "", market_news_label: str = "SAVE PICK") -> dict:
        """
        row: screener.scored_all 한 행(dict)
        news/flow/value: 각 분석 에이전트 결과 dict
        prior_text: 이 종목 점수 구간의 **실측 승률** 문장(tracking.prior.prior_line).
                    conviction 을 추측이 아니라 기저율 조정으로 만들기 위한 입력.
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
            f"[밸류] {value.get('summary')}\n"
            + (f"[실측 기저율] {prior_text}\n" if prior_text else "")
            # 편집자가 고른 당일 주요뉴스. 이 종목 얘기가 아니어도 시장 분위기·
            # 섹터 흐름의 근거가 된다. 종목 무관 사실을 이 종목에 끌어다 붙이지
            # 않도록 "직접 관련 있는 것만 반영"을 명시한다.
            + (f"[오늘 시장 주요뉴스 ({market_news_label or '출처 미상'}) — "
               f"이 종목과 직접 관련 있는 것만 반영, 무관하면 무시]\n"
               f"{market_news}\n" if market_news else "")
            + "\n위를 종합해 추천 JSON을 작성하라. expected_pnl_today는 ATR과 R:R을 활용해 "
            "당일 변동 분포의 P10/P50/P90(%)로 제시하고 basis에 산출 근거를 적어라.")
        result = self.call_json(prompt, RECO_SCHEMA, max_tokens=PM_MAX_TOKENS,
                                thinking=True)
        # 출처 보강: 뉴스 에이전트 sources 합류
        if isinstance(result, dict) and not result.get("_parse_error"):
            srcs = set(result.get("sources", [])) | set(news.get("sources", []))
            result["sources"] = sorted(s for s in srcs if s)
        return result
