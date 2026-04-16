"""
hedge_agent.py — 헷지 전략 판단 에이전트
==========================================
포지션 보유 중 리스크가 높을 때 헷지 전략을 제안.
4가지 전략: 부분청산, 반대포지션, 상관코인 헷지, 전량청산.
AgentBase 상속.
"""

from __future__ import annotations

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage

from risk.models import (
    PipelineContext, RiskAssessment, HedgeRecommendation,
    HedgeAction, Urgency, RiskLevel,
)
from risk import config


class HedgeAgent(AgentBase):
    """
    헷지 전략 판단 에이전트.

    헷지 전략 유형:
    1. PARTIAL_CLOSE: 30~50% 부분 청산 (가장 단순/안전)
    2. OPPOSITE_POSITION: 동일 코인 반대 포지션 (바이낸스 헷지 모드)
    3. CORRELATED_HEDGE: 상관 코인 헷지 (BTC↔ETH 등)
    4. FULL_EXIT: 전량 청산 (극단적 불확실성)
    """

    # 상관 코인 매핑 (헷지 대상)
    CORRELATED_PAIRS = {
        "BTCUSDT": "ETHUSDT",
        "ETHUSDT": "BTCUSDT",
        "SOLUSDT": "ETHUSDT",
        "BNBUSDT": "ETHUSDT",
        "AVAXUSDT": "SOLUSDT",
        "DOGEUSDT": "BTCUSDT",
    }

    def __init__(self):
        super().__init__(
            agent_id="hedge",
            agent_name="헷지 전략가",
            role_description="포지션 리스크 완화를 위한 헷지 전략을 제안하는 에이전트",
        )

    def _get_system_prompt(self) -> str:
        return (
            "당신은 암호화폐 선물 헷지 전략 전문가입니다. "
            "20x 레버리지에서 급격한 변동은 치명적이므로, "
            "적절한 헷지로 리스크를 분산합니다. "
            "불필요한 헷지는 비용(수수료)만 증가시키므로, "
            "리스크가 실제로 높을 때만 헷지를 권고합니다. "
            "응답은 반드시 JSON 형식으로만 하세요."
        )

    def collect_data(self) -> dict:
        return {}

    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
        ctx = self._extract_context(context)
        risk_assessment = self._extract_risk(context)
        result = self._recommend_hedge(ctx, risk_assessment)
        return self._build_message(
            data=result,
            confidence=result.get("confidence", 0.5),
            reasoning=result.get("reasoning", ""),
        )

    def recommend(self, ctx: PipelineContext,
                  risk_assessment: RiskAssessment) -> HedgeRecommendation:
        """파이프라인에서 직접 호출."""
        context = {
            "pipeline_context": ctx,
            "risk_assessment": risk_assessment,
        }
        msg = self.analyze({}, context)
        data = msg.data

        return HedgeRecommendation(
            action=HedgeAction(data.get("action", "none")),
            size_pct=float(data.get("size_pct", 0)),
            hedge_symbol=data.get("hedge_symbol", ""),
            urgency=Urgency(data.get("urgency", "low")),
            exit_condition=data.get("exit_condition", ""),
            reasoning=data.get("reasoning", ""),
            confidence=float(data.get("confidence", 0.5)),
        )

    def _extract_context(self, context: dict | None) -> PipelineContext:
        if context and "pipeline_context" in context:
            pc = context["pipeline_context"]
            return pc if isinstance(pc, PipelineContext) else PipelineContext(**pc)
        return PipelineContext()

    def _extract_risk(self, context: dict | None) -> RiskAssessment:
        if context and "risk_assessment" in context:
            ra = context["risk_assessment"]
            return ra if isinstance(ra, RiskAssessment) else RiskAssessment(**ra)
        return RiskAssessment()

    def _recommend_hedge(self, ctx: PipelineContext,
                         risk: RiskAssessment) -> dict:
        """규칙 기반 헷지 판단 + LLM 보완"""
        # 포지션 없으면 헷지 불필요
        if not ctx.positions:
            return self._no_hedge("보유 포지션 없음")

        # 리스크 낮으면 헷지 불필요
        if risk.portfolio_risk in (RiskLevel.LOW,) and risk.total_risk_score < 40:
            return self._no_hedge(
                f"포트폴리오 리스크 낮음 (점수 {risk.total_risk_score:.0f})"
            )

        # 각 포지션 리스크 확인
        critical_positions = [
            pa for pa in risk.position_assessments
            if pa.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH)
        ]

        if not critical_positions:
            return self._no_hedge("고위험 포지션 없음")

        # 규칙 기반 헷지 결정
        result = self._rule_based_hedge(ctx, risk, critical_positions)

        # LLM 심층 분석 시도
        llm_result = self._llm_hedge(ctx, risk, critical_positions, result)
        if llm_result:
            return llm_result

        return result

    def _rule_based_hedge(self, ctx, risk, critical_positions) -> dict:
        """규칙 기반 헷지 판단"""
        num_critical = sum(
            1 for pa in critical_positions if pa.risk_level == RiskLevel.CRITICAL
        )

        # CRITICAL이 다수 → 전량 청산 권고
        if num_critical >= 2 or risk.total_risk_score >= 90:
            return {
                "action": "full_exit",
                "size_pct": 100.0,
                "hedge_symbol": "",
                "urgency": "high",
                "exit_condition": "시장 안정화 후 재진입",
                "reasoning": (
                    f"CRITICAL 포지션 {num_critical}개, "
                    f"리스크 점수 {risk.total_risk_score:.0f}. 전량 청산 권고."
                ),
                "confidence": 0.7,
            }

        # HIGH 이상 → 부분 청산 또는 반대 포지션
        worst = critical_positions[0]
        worst_symbol = worst.symbol

        # 포지션 크기 확인
        pos = next((p for p in ctx.positions if p.symbol == worst_symbol), None)
        if not pos or pos.size_usdt < config.HEDGE_MIN_POSITION_SIZE:
            return self._no_hedge(f"{worst_symbol} 포지션 크기 부족")

        # 부분 청산 (가장 보수적)
        if risk.total_risk_score >= config.RISK_SCORE_THRESHOLDS["high"]:
            close_pct = min(config.HEDGE_MAX_SIZE_PCT,
                            30 + (risk.total_risk_score - 70))
            return {
                "action": "partial_close",
                "size_pct": round(close_pct, 0),
                "hedge_symbol": "",
                "urgency": "medium",
                "exit_condition": "리스크 점수 50 이하로 하락 시 해제",
                "reasoning": (
                    f"{worst_symbol} 리스크 {worst.risk_level.value}, "
                    f"{close_pct:.0f}% 부분 청산 권고. "
                    f"사유: {worst.reasoning}"
                ),
                "confidence": 0.6,
            }

        # 상관 코인 헷지 검토
        hedge_sym = self.CORRELATED_PAIRS.get(worst_symbol, "")
        if hedge_sym:
            return {
                "action": "correlated_hedge",
                "size_pct": 30.0,
                "hedge_symbol": hedge_sym,
                "urgency": "low",
                "exit_condition": "원래 포지션 청산 시 함께 해제",
                "reasoning": (
                    f"{worst_symbol} 리스크 완화를 위해 "
                    f"{hedge_sym} 반대 방향 30% 헷지 권고."
                ),
                "confidence": 0.5,
            }

        return self._no_hedge("적용 가능한 헷지 전략 없음")

    def _llm_hedge(self, ctx, risk, critical_positions, rule_result) -> dict | None:
        """LLM 심층 헷지 분석"""
        try:
            parts = [
                "현재 포지션과 리스크를 분석하여 헷지 필요성을 판단하세요.\n",
                f"[포트폴리오 리스크] {risk.portfolio_risk.value}, "
                f"점수 {risk.total_risk_score:.0f}/100",
            ]
            for pa in critical_positions[:3]:
                parts.append(f"  [{pa.symbol}] {pa.risk_level.value} — {pa.reasoning}")

            parts.append(f"\n[규칙 기반 판단] {rule_result.get('action', 'none')}: "
                         f"{rule_result.get('reasoning', '')}")

            parts.append(
                "\nJSON으로 응답 (action은 none/partial_close/opposite_position/"
                "correlated_hedge/full_exit 중 하나):\n"
                '{"action": "", "size_pct": 0, "hedge_symbol": "", '
                '"urgency": "low/medium/high", "exit_condition": "", '
                '"reasoning": "", "confidence": 0.0~1.0}'
            )

            result = self.llm_json("\n".join(parts), deep=False,
                                    max_tokens=config.HEDGE_MAX_TOKENS)
            if result.get("parse_error") or "action" not in result:
                return None

            # action 유효성 검증
            valid_actions = {"none", "partial_close", "opposite_position",
                             "correlated_hedge", "full_exit"}
            if result.get("action") not in valid_actions:
                result["action"] = rule_result.get("action", "none")

            return result

        except Exception as e:
            print(f"  [HedgeAgent] LLM 분석 실패: {e}")
            return None

    def _no_hedge(self, reason: str) -> dict:
        return {
            "action": "none",
            "size_pct": 0.0,
            "hedge_symbol": "",
            "urgency": "low",
            "exit_condition": "",
            "reasoning": f"헷지 불필요: {reason}",
            "confidence": 0.8,
        }
