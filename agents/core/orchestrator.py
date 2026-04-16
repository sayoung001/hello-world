"""
orchestrator.py — 토론 오케스트레이터
======================================
Market Context Layer (Agent 1~6) → Bull/Bear 토론 → Moderator → Position Layer 흐름 관리.

차용 아이디어:
- TradingAgents: 구조화 리포트 + 선택적 토론
- AI Hedge Fund: 갈등하는 철학의 에이전트 토론 (Bull vs Bear)
- FS-ReasoningAgent: Factual vs Subjective 분리 + 시장별 동적 가중치
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

# ── 시스템 프롬프트 ──

MODERATOR_PROMPT = """당신은 크립토 선물 트레이딩 전문가 패널의 모더레이터입니다.
각 에이전트의 분석 + Bull/Bear 토론 결과를 종합하여 최종 컨센서스를 도출합니다.

규칙:
1. Factual 에이전트 (BTC 구조, 상관관계)는 기초 사실 → 높은 가중치
2. Subjective 에이전트 (매크로, 알트, 뉴스)는 해석 → 보조 참고
3. Bull/Bear 토론에서 양측의 근거를 공정하게 평가 (Bear 편향 주의)
4. 의견 충돌 시 데이터 기반으로 판단 — 단순히 보수적이면 안 됨. 리스크가 실제로 높을 때만 DANGER
5. 반드시 JSON 형식으로 출력

출력 형식:
```json
{
  "overall_risk": "SAFE|CAUTION|DANGER",
  "market_regime": "risk-on|neutral|risk-off",
  "btc_bias": "bullish|neutral|bearish",
  "confidence": 0.0~1.0,
  "key_warnings": ["경고1", "경고2"],
  "bull_bear_verdict": "bull_win|bear_win|draw",
  "reasoning": "종합 판단 근거"
}
```"""

BULL_PROMPT = """당신은 강세론자(Bull)입니다. 에이전트 분석 결과를 보고 시장이 왜 괜찮은지,
현재 포지션을 유지/확대해야 하는 이유를 200자 이내로 강하게 주장하세요.
단, 근거 없는 낙관은 금지. 데이터에서 긍정 시그널을 찾아 인용하세요."""

BEAR_PROMPT = """당신은 약세론자(Bear)입니다. 에이전트 분석 결과를 보고 왜 위험한지,
포지션을 축소/청산해야 하는 이유를 200자 이내로 강하게 주장하세요.
특히 20x 레버리지 환경에서 간과된 리스크를 찾아내세요. 생존이 최우선입니다."""

# ── 동적 가중치: 시장 상황별 Factual/Subjective 가중치 조정 ──
#    하락장 → Factual 에이전트(BTC 구조, 상관관계) 가중치 ↑
#    상승장 → 균등
REGIME_WEIGHTS = {
    # (factual_weight, subjective_weight) — 합이 1.0
    "risk-off":  (0.65, 0.35),   # 하락장: Factual 신뢰 ↑
    "neutral":   (0.55, 0.45),   # 중립: 약간 Factual 우세
    "risk-on":   (0.50, 0.50),   # 상승장: 균등
}


class Orchestrator:
    """
    멀티 에이전트 토론 오케스트레이터

    실행 흐름:
    1. Market Context Layer: Agent 1~6 순차 실행 → 각자 분석
    2. Bull/Bear 토론: LLM이 강세/약세 시각에서 분석 결과를 토론
    3. Moderator: 토론 결과 + 동적 가중치 → 컨센서스 도출
    4. Position Layer: Agent 5가 포지션별 판결
    """

    def __init__(self):
        self.context_agents: list[AgentBase] = []
        self.position_agent: AgentBase | None = None
        self._last_consensus: MarketConsensus | None = None

    def register_context_agent(self, agent: AgentBase):
        self.context_agents.append(agent)

    def register_position_agent(self, agent: AgentBase):
        self.position_agent = agent

    # ── Phase 1: Context Layer ──

    def run_context_layer(self, crash_context: dict | None = None) -> list[AgentMessage]:
        """각 에이전트가 독립적으로 데이터 수집 + 분석"""
        messages: list[AgentMessage] = []
        for agent in self.context_agents:
            try:
                msg = agent.run(crash_context=crash_context)
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

    # ── Phase 2a: Bull/Bear 토론 ──

    def _run_bull_bear_debate(self, agent_messages: list[AgentMessage],
                              crash_context: dict | None = None) -> dict:
        """
        Bull vs Bear 토론 (AI Hedge Fund 아이디어).
        각 에이전트 분석을 보고 강세/약세 시각에서 토론.
        """
        # 분석 요약 생성
        summary = self._build_analysis_summary(agent_messages)

        # 급락 컨텍스트가 있으면 토론에 반영
        crash_text = ""
        if crash_context:
            crash_text = (
                f"\n\n## ⚠️ 급락 상황\n"
                f"BTC {crash_context.get('window', '')} {crash_context.get('change_pct', 0):+.2f}% 급락\n"
                f"현재가: ${crash_context.get('current_price', 0):,.0f}\n"
                f"20x ROE 영향: {crash_context.get('roe_impact', 0):+.1f}%\n"
                f"청산까지: {crash_context.get('remaining_to_liq', 5.0):.2f}%\n"
                f"이 급락 상황을 반드시 고려하여 토론하세요."
            )

        try:
            import anthropic
            client = anthropic.Anthropic()

            debate_input = f"에이전트 분석:\n{summary}{crash_text}"

            # Bull 주장
            bull_resp = client.messages.create(
                model=DEEP_MODEL, max_tokens=500,
                system=BULL_PROMPT,
                messages=[{"role": "user", "content": debate_input}]
            )
            bull_argument = bull_resp.content[0].text.strip()

            # Bear 주장
            bear_resp = client.messages.create(
                model=DEEP_MODEL, max_tokens=500,
                system=BEAR_PROMPT,
                messages=[{"role": "user", "content": debate_input}]
            )
            bear_argument = bear_resp.content[0].text.strip()

            print(f"  🐂 Bull: {bull_argument[:80]}...")
            print(f"  🐻 Bear: {bear_argument[:80]}...")

            return {
                "bull": bull_argument,
                "bear": bear_argument,
                "debate_completed": True,
            }
        except Exception as e:
            print(f"  ⚠️ Bull/Bear 토론 실패 (폴백): {e}")
            return self._rule_based_debate(agent_messages)

    def _rule_based_debate(self, messages: list[AgentMessage]) -> dict:
        """규칙 기반 Bull/Bear 폴백"""
        bull_points = []
        bear_points = []

        for msg in messages:
            risk = msg.data.get("risk_level", msg.data.get("overall_risk", ""))
            if risk == "SAFE":
                bull_points.append(f"{msg.agent_name}: 안전 판단")
            elif risk == "DANGER":
                bear_points.append(f"{msg.agent_name}: 위험 감지")
            if msg.data.get("trend") == "bullish" or msg.data.get("btc_bias") == "bullish":
                bull_points.append(f"{msg.agent_name}: 상승 트렌드")
            if msg.data.get("trend") == "bearish" or msg.data.get("btc_bias") == "bearish":
                bear_points.append(f"{msg.agent_name}: 하락 트렌드")
            for w in msg.warnings:
                bear_points.append(f"경고: {w[:50]}")

        # 감성/뉴스 기반
        for msg in messages:
            sentiment = msg.data.get("overall_sentiment", "")
            if sentiment in ("panic", "very_bearish"):
                bear_points.append(f"{msg.agent_name}: 시장 공포")
            elif sentiment in ("bullish", "very_bullish"):
                bull_points.append(f"{msg.agent_name}: 시장 낙관")
            geo_risk = msg.data.get("geopolitical_risk", "")
            if geo_risk in ("high", "critical"):
                bear_points.append(f"{msg.agent_name}: 지정학 리스크 {geo_risk}")

        return {
            "bull": " | ".join(bull_points[:5]) if bull_points else "긍정 시그널 부족",
            "bear": " | ".join(bear_points[:5]) if bear_points else "뚜렷한 위험 없음",
            "debate_completed": False,
        }

    # ── Phase 2b: 동적 가중치 적용 ──

    def _apply_dynamic_weights(self, messages: list[AgentMessage],
                                 regime: str = "neutral") -> list[AgentMessage]:
        """
        시장 상황별 Factual/Subjective 에이전트 가중치 조정.
        하락장(risk-off) → Factual 에이전트 confidence 상향
        """
        factual_w, subjective_w = REGIME_WEIGHTS.get(regime, (0.55, 0.45))

        weighted = []
        for msg in messages:
            analysis_type = getattr(
                next((a for a in self.context_agents if a.agent_id == msg.agent_id), None),
                '_analysis_type', 'subjective'
            )

            weight = factual_w if analysis_type == "factual" else subjective_w
            # 가중치를 confidence에 반영 (기존 confidence × weight × 2로 정규화)
            adjusted_conf = min(msg.confidence * weight * 2, 1.0)

            weighted.append(AgentMessage(
                agent_id=msg.agent_id,
                agent_name=msg.agent_name,
                confidence=adjusted_conf,
                data=msg.data,
                reasoning=msg.reasoning,
                warnings=msg.warnings,
            ))

        return weighted

    # ── Phase 2c: Moderator (종합) ──

    def moderate(self, agent_messages: list[AgentMessage],
                 crash_context: dict | None = None) -> MarketConsensus:
        """
        전체 모더레이션 파이프라인:
        1. 초기 regime 추정 (규칙 기반)
        2. 동적 가중치 적용
        3. Bull/Bear 토론
        4. LLM 또는 규칙 기반 최종 컨센서스
        """
        all_warnings = []
        for msg in agent_messages:
            all_warnings.extend(msg.warnings)

        # 1. 초기 regime 추정
        estimated_regime = self._estimate_regime(agent_messages)
        # 급락 시 regime을 risk-off로 강제 보정
        if crash_context and crash_context.get("level", 0) >= 2:
            estimated_regime = "risk-off"
        print(f"  📊 추정 regime: {estimated_regime}")

        # 2. 동적 가중치 적용
        weighted_messages = self._apply_dynamic_weights(agent_messages, estimated_regime)

        # 3. Bull/Bear 토론
        print("\n🐂🐻 [Phase 2a] Bull/Bear 토론")
        debate = self._run_bull_bear_debate(agent_messages, crash_context=crash_context)

        # 4. LLM 모더레이션
        try:
            import anthropic
            client = anthropic.Anthropic()

            summary = self._build_analysis_summary(weighted_messages)
            debate_text = (f"\n\n## Bull/Bear 토론\n"
                           f"🐂 Bull: {debate['bull']}\n"
                           f"🐻 Bear: {debate['bear']}")

            # 급락 컨텍스트를 모더레이터에게 전달
            crash_instruction = ""
            if crash_context:
                crash_instruction = (
                    f"\n\n## ⚠️ 긴급: 급락 발생 중\n"
                    f"BTC {crash_context.get('window', '')} {crash_context.get('change_pct', 0):+.2f}% 급락\n"
                    f"현재가: ${crash_context.get('current_price', 0):,.0f}, "
                    f"20x ROE 영향: {crash_context.get('roe_impact', 0):+.1f}%\n"
                    f"청산까지: {crash_context.get('remaining_to_liq', 5.0):.2f}%\n"
                    f"급락 원인과 향후 전망을 반드시 판단에 반영하세요. "
                    f"20x 레버리지 생존이 최우선입니다."
                )

            response = client.messages.create(
                model=DEEP_MODEL,
                max_tokens=1500,
                system=MODERATOR_PROMPT,
                messages=[{"role": "user", "content": (
                    f"에이전트 분석 (가중치 적용됨):\n{summary}"
                    f"{debate_text}{crash_instruction}\n\n"
                    f"위 분석과 토론을 종합하여 최종 컨센서스를 도출하세요.\n"
                    f"현재 추정 regime: {estimated_regime}"
                )}]
            )
            raw = response.content[0].text
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            result = json.loads(raw.strip())

            consensus = MarketConsensus(
                overall_risk=RiskLevel(result.get("overall_risk", "SAFE")),
                market_regime=MarketRegime(result.get("market_regime", "neutral")),
                btc_bias=result.get("btc_bias", "neutral"),
                confidence=result.get("confidence", 0.5),
                warnings=result.get("key_warnings", []) + all_warnings,
                agent_messages=agent_messages,
            )
            # 토론 결과를 consensus에 첨부
            consensus.debate = debate
            consensus.bull_bear_verdict = result.get("bull_bear_verdict", "draw")
            return consensus

        except Exception as e:
            print(f"  ⚠️ LLM 모더레이션 실패, 규칙 기반 폴백: {e}")
            return self._rule_based_moderate(weighted_messages, all_warnings, debate)

    def _estimate_regime(self, messages: list[AgentMessage]) -> str:
        """에이전트 분석에서 regime 초기 추정"""
        danger_count = 0
        safe_count = 0
        bearish_signals = 0

        for msg in messages:
            risk = msg.data.get("risk_level", msg.data.get("overall_risk", ""))
            if risk == "DANGER":
                danger_count += 1
            elif risk == "SAFE":
                safe_count += 1

            # 감성/뉴스 시그널
            sentiment = msg.data.get("overall_sentiment", "")
            if sentiment in ("panic", "very_bearish"):
                bearish_signals += 2
            elif sentiment == "bearish":
                bearish_signals += 1

            # 지정학 리스크
            geo = msg.data.get("geopolitical_risk", "")
            if geo in ("high", "critical"):
                bearish_signals += 2

            # 매크로
            appetite = msg.data.get("risk_appetite", "")
            if appetite == "risk-off":
                bearish_signals += 1

        if danger_count >= 3 or bearish_signals >= 4:
            return "risk-off"
        elif safe_count >= len(messages) * 0.4:  # 40%로 완화 (기존 60%)
            return "risk-on"
        return "neutral"

    def _rule_based_moderate(self, messages: list[AgentMessage],
                              warnings: list[str],
                              debate: dict = None) -> MarketConsensus:
        """규칙 기반 폴백 (동적 가중치 반영된 messages 사용)"""
        # 가중치 적용된 confidence로 weighted vote
        total_weight = sum(m.confidence for m in messages) or 1
        danger_weight = sum(m.confidence for m in messages
                            if m.data.get("risk_level") == "DANGER"
                            or m.data.get("overall_risk") == "DANGER")
        caution_weight = sum(m.confidence for m in messages
                              if m.data.get("risk_level") == "CAUTION"
                              or m.data.get("overall_risk") == "CAUTION")

        danger_pct = danger_weight / total_weight
        caution_pct = caution_weight / total_weight

        if danger_pct >= 0.45:  # 45% 이상이 DANGER (기존 30% → 완화)
            risk = RiskLevel.DANGER
        elif danger_pct + caution_pct >= 0.55:  # 55% 이상이 CAUTION+ (기존 40% → 완화)
            risk = RiskLevel.CAUTION
        else:
            risk = RiskLevel.SAFE

        # BTC bias 추출
        btc_bias = "neutral"
        for msg in messages:
            if "BTC" in msg.agent_name or "btc" in msg.agent_id:
                btc_bias = msg.data.get("trend", msg.data.get("btc_bias", "neutral"))
                break

        avg_conf = sum(m.confidence for m in messages) / max(len(messages), 1)

        consensus = MarketConsensus(
            overall_risk=risk,
            market_regime=MarketRegime.NEUTRAL,
            btc_bias=btc_bias,
            confidence=avg_conf,
            warnings=warnings,
            agent_messages=messages,
        )
        if debate:
            consensus.debate = debate
        return consensus

    # ── Phase 3: Position Layer ──

    def run_position_layer(self, consensus: MarketConsensus,
                           positions: list[PositionInfo],
                           crash_context: dict | None = None) -> list[PositionVerdict]:
        if not self.position_agent:
            print("  ⚠️ 포지션 에이전트 미등록")
            return []

        context = {
            "consensus": {
                "overall_risk": consensus.overall_risk.value,
                "market_regime": consensus.market_regime.value,
                "btc_bias": consensus.btc_bias,
                "confidence": consensus.confidence,
                "warnings": consensus.warnings,
                "debate": getattr(consensus, 'debate', {}),
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

        # 급락 컨텍스트가 있으면 포지션 심판에 전달
        if crash_context:
            context["crash"] = {
                "level": crash_context.get("level", 0),
                "change_pct": crash_context.get("change_pct", 0),
                "roe_impact": crash_context.get("roe_impact", 0),
                "remaining_to_liq": crash_context.get("remaining_to_liq", 5.0),
                "window": crash_context.get("window", ""),
            }

        msg = self.position_agent.run(context=context)
        consensus.position_verdicts = msg.data.get("verdicts", [])
        return consensus.position_verdicts

    # ── 전체 파이프라인 ──

    def run(self, positions: list[PositionInfo] | None = None,
            crash_context: dict | None = None) -> MarketConsensus:
        """
        1. Context Layer (Agent 1~6) → 각자 분석
        2. Bull/Bear 토론 + 동적 가중치 → 컨센서스
        3. Position Layer (Agent 5) → 포지션별 판결

        :param positions: 현재 보유 포지션 리스트
        :param crash_context: 급락 감지 정보 (긴급 분석 시 CrashDetector에서 전달)
        """
        is_emergency = crash_context is not None
        label = "🚨 긴급 멀티 에이전트 분석" if is_emergency else "🔍 멀티 에이전트 시장 분석"

        print(f"\n{'='*60}")
        print(f"{label} 시작")
        if is_emergency:
            print(f"  급락: BTC {crash_context.get('window', '')} "
                  f"{crash_context.get('change_pct', 0):+.2f}% "
                  f"(ROE {crash_context.get('roe_impact', 0):+.0f}%)")
        print(f"{'='*60}")
        start = time.time()

        # 1. Context Layer (급락 컨텍스트 전달)
        print("\n📊 [Phase 1] Market Context Layer")
        agent_messages = self.run_context_layer(crash_context=crash_context)

        # 2. Moderation (Bull/Bear + 동적 가중치 포함)
        print("\n🤝 [Phase 2] 컨센서스 도출 (Bull/Bear 토론 + 동적 가중치)")
        consensus = self.moderate(agent_messages, crash_context=crash_context)
        debate = getattr(consensus, 'debate', {})
        verdict = getattr(consensus, 'bull_bear_verdict', 'N/A')
        print(f"  → 종합 리스크: {consensus.overall_risk.value}")
        print(f"  → 시장 상태: {consensus.market_regime.value}")
        print(f"  → BTC 편향: {consensus.btc_bias}")
        print(f"  → Bull/Bear: {verdict}")

        # 3. Position Layer (급락 시에는 포지션 없어도 실행)
        if positions or is_emergency:
            n = len(positions) if positions else 0
            print(f"\n⚖️ [Phase 3] 포지션 심판 ({n}개)")
            if positions:
                self.run_position_layer(consensus, positions, crash_context=crash_context)

        elapsed = time.time() - start
        print(f"\n✅ 분석 완료 ({elapsed:.1f}초)")
        print(f"{'='*60}\n")

        self._last_consensus = consensus
        return consensus

    # ── 헬퍼 ──

    @staticmethod
    def _build_analysis_summary(messages: list[AgentMessage]) -> str:
        """에이전트 분석 요약 텍스트"""
        parts = []
        for msg in messages:
            parts.append(
                f"[{msg.agent_name}] (confidence: {msg.confidence:.2f})\n"
                f"  분석: {json.dumps(msg.data, ensure_ascii=False)[:400]}\n"
                f"  추론: {msg.reasoning}"
            )
        return "\n\n".join(parts)
