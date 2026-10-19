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
    failures: dict[str, str] = field(default_factory=dict)     # {종목: 실패 사유}
    usage: dict[str, int] = field(default_factory=dict)        # 토큰 사용량 합계


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
        # ── 종목 단위 격리 ──
        # 예전에는 호출부(daily_batch)도 여기도 예외를 막지 않아서, 60회 호출 중
        # 한 번만 실패해도 그날 추천·Notion·Telegram·신호기록이 전부 날아갔다.
        # 1종목 실패가 나머지를 죽이지 않게 한다.
        try:
            n = news_a.analyze(sym, news_map.get(sym, []))
            f = flow_a.analyze(sym, r)
            v = value_a.analyze(sym, r)
            label = sector_label_map.get(sym, "-")
            reco = pm.decide(r, n, f, v, sector_label=label)
        except Exception as e:  # noqa: BLE001 — 1종목 실패를 격리
            msg = f"{type(e).__name__}: {e}"
            print(f"[agents] {sym} 분석 실패 — 건너뜁니다 ({msg})")
            out.failures[sym] = msg
            continue
        reconcile_levels(reco, r)
        out.usage = _merge_usage(out.usage, _collect_usage(n, f, v, reco))

        # ── 파싱 실패분은 하류로 보내지 않는다 ──
        # 예전에는 무조건 append 해서, 파싱 실패 dict 가 Notion 에 '종목 -' 같은
        # 쓰레기 행으로 올라가고 다이제스트에도 빈 추천으로 찍혔다.
        if not isinstance(reco, dict) or reco.get("_parse_error"):
            raw = (reco or {}).get("_raw", "") if isinstance(reco, dict) else ""
            print(f"[agents] {sym} 추천 JSON 파싱 실패 — 제외 "
                  f"(앞부분: {str(raw)[:80]!r})")
            out.failures[sym] = "parse_error"
            continue

        out.per_ticker[sym] = {"news": n, "flow": f, "value": v}
        out.recommendations.append(reco)

    if out.failures:
        print(f"[agents] 성공 {len(out.recommendations)}건 · "
              f"실패 {len(out.failures)}건 {list(out.failures)}")
    if out.usage:
        print(f"[agents] 토큰 사용 {out.usage}")
    return out


def _collect_usage(*results) -> dict:
    """에이전트 결과에 붙은 _usage 를 모은다(call_json 이 넣는다)."""
    tot: dict[str, int] = {}
    for d in results:
        if isinstance(d, dict):
            tot = _merge_usage(tot, d.pop("_usage", {}) or {})
    return tot


def _merge_usage(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in (b or {}).items():
        try:
            out[k] = out.get(k, 0) + int(v)
        except (TypeError, ValueError):
            continue
    return out


def reconcile_levels(reco: dict, row: dict) -> dict:
    """
    LLM이 낸 손절/목표가를 검증하고, 비정상이면 규칙엔진 값으로 되돌린다.

    LLM 출력은 스키마상 number면 통과하므로 0·음수·종가 반대편 값이 그대로 들어올 수
    있다. 그대로 두면 (a) Notion에 손절 0 이 게시되고 (b) 라벨러가 '진입가가 이미
    목표 위'로 판단해 전 건을 skip 처리 → 캘리브레이션 표본이 0이 된다.
    조용히 통과시키지 않고 여기서 한 번 걸러낸다.
    """
    if not isinstance(reco, dict) or reco.get("_parse_error"):
        return reco
    close = _num(row.get("close"))
    if close is None or close <= 0:
        return reco

    stop = _num(reco.get("stop"))
    if stop is None or not (0 < stop < close):          # 손절은 종가 아래
        reco["stop"] = _num(row.get("stop"))

    targets = [t for t in (_num(x) for x in (reco.get("targets") or []))
               if t is not None and t > close]          # 목표는 종가 위
    if not targets:
        rt = _num(row.get("target"))
        targets = [rt] if rt is not None and rt > close else []
    reco["targets"] = targets

    # 진입 구간도 같은 이유로 검증 — 종가의 ±30% 밖은 오기로 본다
    zone = [z for z in (_num(x) for x in (reco.get("entry_zone") or []))
            if z is not None and 0.7 * close <= z <= 1.3 * close]
    reco["entry_zone"] = zone or [round(close, 4)]
    return reco


def _num(v) -> Optional[float]:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


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
