"""
orchestrator.py — 토론 오케스트레이터
======================================
Market Context Layer (Agent 1~4) → Moderator → Position Layer (Agent 5) 흐름 관리.
"""

from __future__ import annotations
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from agents.core.base import AgentBase, DEEP_MODEL
from agents.core.protocol import (
    AgentMessage, MarketConsensus, PositionInfo, PositionVerdict,
    RiskLevel, MarketRegime, ActionType,
)

KST = timezone(timedelta(hours=9))

# 토론 시스템 프롬프트 (설계문서 Section 4)
DISCUSSION_SYSTEM_PROMPT = """당신들은 20배 레버리지 선물 트레이딩에 대해 토론하는 전문가 패널입니다.

핵심 제약:
- 20배 레버리지 = 약 4% 역행 시 청산
- 따라서 모든 판단은 "높은 확률"보다 "생존"이 우선
- 의견이 갈리면 보수적 판단을 우선

토론 규칙:
1. 각 에이전트는 자신의 분석을 먼저 제시
2. 다른 에이전트의 분석과 충돌하는 부분을 지적
3. 특히 "내 분석에서는 괜찮아 보이지만, Agent X의 우려가 맞다면 위험"을 명시
4. 최종적으로 각자의 신뢰도(0-1)와 함께 포지션별 권고안 제시

20배 기준 리스크 분류:
- SAFE: 청산가까지 충분한 여유, 시장 환경 우호적
- CAUTION: 하나 이상의 불확실성 존재, 포지션 축소 권고
- DANGER: 복수의 위험 신호, 즉시 청산 또는 대폭 축소 권고"""

MODERATOR_PROMPT = """당신은 크립토 선물 트레이딩 전문가 패널의 모더레이터입니다.
각 에이전트의 분석 결과를 종합하여 최종 컨센서스를 도출합니다.

규칙:
1. Factual 에이전트 (BTC 구조, 상관관계)의 데이터를 기준으로 판단
2. Subjective 에이전트 (알트 생태계, 매크로)의 의견은 보조 참고
3. 의견 충돌 시 보수적 판단 우선 (20x 레버리지 생존 원칙)
4. 반드시 JSON 형식으로 출력

출력 형식:
```json
{
  "overall_risk": "SAFE|CAUTION|DANGER",
  "market_regime": "risk-on|neutral|risk-off",
  "btc_bias": "bullish|neutral|bearish",
  "confidence": 0.0~1.0,
  "key_warnings": ["경고1", "경고2"],
  "reasoning": "종합 판단 근거"
}
```"""


class Orchestrator:
    """
    멀티 에이전트 토론 오케스트레이터

    실행 흐름:
    1. Market Context Layer: Agent 1~4 병렬 실행 → 각자 분석
    2. Moderator: 분석 결과 종합 → MarketConsensus 도출
    3. Position Layer: Agent 5가 포지션별 판결
    4. 텔레그램 출력
    """

    def __init__(self):
        self.context_agents: list[AgentBase] = []   # Agent 1~4
        self.position_agent: AgentBase | None = None  # Agent 5
        self._last_consensus: MarketConsensus | None = None

    def register_context_agent(self, agent: AgentBase):
        """Market Context Layer에 에이전트 등록"""
        self.context_agents.append(agent)

    def register_position_agent(self, agent: AgentBase):
        """Position Layer에 에이전트 등록"""
        self.position_agent = agent

    def run_context_layer(self) -> list[AgentMessage]:
        """
        Market Context Layer 실행
        각 에이전트가 독립적으로 데이터 수집 + 분석 수행
        """
        messages: list[AgentMessage] = []
        for agent in self.context_agents:
            try:
                msg = agent.run()
                messages.append(msg)
                print(f"  ✅ {agent.agent_name} 분석 완료 (confidence: {msg.confidence:.2f})")
            except Exception as e:
                print(f"  ❌ {agent.agent_name} 실패: {e}")
                messages.append(AgentMessage(
                    agent_id=agent.agent_id,
                    agent_name=agent.agent_name,
                    confidence=0.0,
                    data={"error": str(e)},
                    warnings=[f"{agent.agent_name} 분석 실패"]
                ))
        return messages

    def moderate(self, agent_messages: list[AgentMessage]) -> MarketConsensus:
        """
        Moderator: 에이전트 분석 종합 → 컨센서스 도출

        LLM을 사용하여 충돌하는 의견을 중재하고 최종 판단 생성.
        LLM 호출 실패 시 규칙 기반 폴백 사용.
        """
        # 에이전트 분석 요약 생성
        summaries = []
        all_warnings = []
        for msg in agent_messages:
            summaries.append(
                f"[{msg.agent_name}] (confidence: {msg.confidence:.2f})\n"
                f"  분석: {json.dumps(msg.data, ensure_ascii=False, indent=2)}\n"
                f"  추론: {msg.reasoning}"
            )
            all_warnings.extend(msg.warnings)

        analysis_text = "\n\n".join(summaries)

        # LLM 모더레이션 시도
        try:
            import anthropic  # noqa: F811
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=DEEP_MODEL,
                max_tokens=1500,
                system=MODERATOR_PROMPT,
                messages=[{"role": "user", "content": (
                    f"다음 에이전트들의 분석 결과를 종합하여 최종 컨센서스를 도출하세요.\n\n"
                    f"{analysis_text}"
                )}]
            )
            raw = response.content[0].text
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            result = json.loads(raw.strip())

            return MarketConsensus(
                overall_risk=RiskLevel(result.get("overall_risk", "CAUTION")),
                market_regime=MarketRegime(result.get("market_regime", "neutral")),
                btc_bias=result.get("btc_bias", "neutral"),
                confidence=result.get("confidence", 0.5),
                warnings=result.get("key_warnings", []) + all_warnings,
                agent_messages=agent_messages,
            )
        except Exception as e:
            print(f"  ⚠️ LLM 모더레이션 실패, 규칙 기반 폴백: {e}")
            return self._rule_based_moderate(agent_messages, all_warnings)

    def _rule_based_moderate(self, messages: list[AgentMessage],
                              warnings: list[str]) -> MarketConsensus:
        """규칙 기반 폴백 모더레이션 (LLM 실패 시)"""
        avg_conf = sum(m.confidence for m in messages) / max(len(messages), 1)

        # DANGER가 하나라도 있으면 전체 CAUTION 이상
        has_danger = any(
            m.data.get("risk_level") == "DANGER" or
            m.data.get("overall_risk") == "DANGER"
            for m in messages
        )
        has_caution = any(
            m.data.get("risk_level") == "CAUTION" or
            m.data.get("overall_risk") == "CAUTION"
            for m in messages
        )

        if has_danger:
            risk = RiskLevel.DANGER
        elif has_caution:
            risk = RiskLevel.CAUTION
        else:
            risk = RiskLevel.SAFE

        return MarketConsensus(
            overall_risk=risk,
            market_regime=MarketRegime.NEUTRAL,
            btc_bias="neutral",
            confidence=avg_conf,
            warnings=warnings,
            agent_messages=messages,
        )

    def run_position_layer(self, consensus: MarketConsensus,
                           positions: list[PositionInfo]) -> list[PositionVerdict]:
        """
        Position Layer 실행
        Agent 5가 각 포지션에 대해 종합 판결 수행
        """
        if not self.position_agent:
            print("  ⚠️ 포지션 에이전트 미등록")
            return []

        # 포지션 정보 + 컨센서스를 컨텍스트로 전달
        context = {
            "consensus": {
                "overall_risk": consensus.overall_risk.value,
                "market_regime": consensus.market_regime.value,
                "btc_bias": consensus.btc_bias,
                "confidence": consensus.confidence,
                "warnings": consensus.warnings,
            },
            "positions": [
                {
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "pnl_pct": p.pnl_pct,
                    "roe_pct": p.roe_pct,
                    "liquidation_distance_pct": p.liquidation_distance_pct,
                    "risk_level": p.risk_level.value,
                    "hold_candles": p.hold_candles,
                }
                for p in positions
            ],
            "agent_analyses": [
                {
                    "agent": m.agent_name,
                    "data": m.data,
                    "confidence": m.confidence,
                }
                for m in consensus.agent_messages
            ],
        }

        msg = self.position_agent.run(context=context)
        consensus.position_verdicts = msg.data.get("verdicts", [])
        return consensus.position_verdicts

    def run(self, positions: list[PositionInfo] | None = None) -> MarketConsensus:
        """
        전체 파이프라인 실행

        1. Context Layer (Agent 1~4) → 병렬 분석
        2. Moderator → 컨센서스 도출
        3. Position Layer (Agent 5) → 포지션별 판결
        """
        print(f"\n{'='*60}")
        print(f"🔍 멀티 에이전트 시장 분석 시작")
        print(f"{'='*60}")
        start = time.time()

        # 1. Context Layer
        print("\n📊 [Phase 1] Market Context Layer")
        agent_messages = self.run_context_layer()

        # 2. Moderation
        print("\n🤝 [Phase 2] 컨센서스 도출")
        consensus = self.moderate(agent_messages)
        print(f"  → 종합 리스크: {consensus.overall_risk.value}")
        print(f"  → 시장 상태: {consensus.market_regime.value}")
        print(f"  → BTC 편향: {consensus.btc_bias}")

        # 3. Position Layer
        if positions:
            print(f"\n⚖️ [Phase 3] 포지션 심판 ({len(positions)}개)")
            self.run_position_layer(consensus, positions)

        elapsed = time.time() - start
        print(f"\n✅ 분석 완료 ({elapsed:.1f}초)")
        print(f"{'='*60}\n")

        self._last_consensus = consensus
        return consensus
