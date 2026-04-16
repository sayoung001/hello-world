"""
leverage_agent.py — 레버리지 동적 조절 에이전트
=================================================
시장 변동성, BTC 상태, 포지션 리스크에 따라
레버리지를 10x~20x 범위에서 동적 조절.
AgentBase 상속.
"""

from __future__ import annotations

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage

from risk.models import (
    PipelineContext, RiskAssessment, LeverageRecommendation,
    RiskLevel,
)
from risk import config


class LeverageAgent(AgentBase):
    """
    레버리지 동적 조절 에이전트.

    핵심 로직:
    - ATR% 기반 변동성 → 레버리지 역상관
    - BTC 필터 레벨 4+ → 감산
    - 극단 펀딩레이트 → 감산
    - 연속 손실 3회+ → 감산
    - Vision AI high risk → 감산
    """

    def __init__(self):
        super().__init__(
            agent_id="leverage",
            agent_name="레버리지 조절자",
            role_description="시장 상황에 따라 레버리지를 10x~20x 동적 조절하는 에이전트",
        )

    def _get_system_prompt(self) -> str:
        return (
            "당신은 암호화폐 선물 레버리지 관리 전문가입니다. "
            "변동성이 높을 때는 레버리지를 낮추고, "
            "안정적일 때는 레버리지를 높여 수익을 극대화합니다. "
            "10x~20x 범위 내에서만 조절합니다."
        )

    def collect_data(self) -> dict:
        return {}

    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
        ctx = self._extract_context(context)
        risk_assessment = self._extract_risk(context)
        result = self._recommend_leverage(ctx, risk_assessment)
        return self._build_message(
            data=result,
            confidence=result.get("confidence", 0.5),
            reasoning=result.get("adjustment_reason", ""),
        )

    def recommend(self, ctx: PipelineContext,
                  risk_assessment: RiskAssessment) -> LeverageRecommendation:
        """파이프라인에서 직접 호출."""
        context = {
            "pipeline_context": ctx,
            "risk_assessment": risk_assessment,
        }
        msg = self.analyze({}, context)
        data = msg.data

        return LeverageRecommendation(
            recommended_leverage=int(data.get("recommended_leverage", 20)),
            current_leverage=int(data.get("current_leverage", 20)),
            adjustment_reason=data.get("adjustment_reason", ""),
            risk_factors=data.get("risk_factors", []),
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

    def _recommend_leverage(self, ctx: PipelineContext,
                            risk: RiskAssessment) -> dict:
        """규칙 기반 레버리지 결정"""
        market = ctx.market_state
        leverage = config.LEVERAGE_DEFAULT  # 20x 시작
        risk_factors = []
        penalties = []

        # 1. ATR% → 레버리지 매핑
        atr = market.btc_atr_pct
        for threshold, lev in sorted(config.LEVERAGE_ATR_MAP.items()):
            if atr <= threshold:
                leverage = lev
                break
        if atr > 2.0:
            risk_factors.append(f"높은 변동성 ATR {atr:.1f}%")

        # 2. BTC 필터 레벨 4+
        if market.btc_level >= 4:
            penalty = config.LEVERAGE_PENALTY["btc_level_4_plus"]
            leverage += penalty
            penalties.append(f"BTC 레벨 {market.btc_level} ({penalty}x)")
            risk_factors.append(f"BTC 필터 레벨 {market.btc_level}")

        # 3. 극단 펀딩레이트
        if abs(market.funding_rate) > 0.05:
            penalty = config.LEVERAGE_PENALTY["extreme_funding"]
            leverage += penalty
            penalties.append(f"극단 펀딩 {market.funding_rate:.4f}% ({penalty}x)")
            risk_factors.append(f"극단 펀딩레이트 {market.funding_rate:.4f}%")

        # 4. Vision AI 리스크
        vision = ctx.vision_features
        if vision and vision.get("vision_risk_level", 0) > 0.7:
            penalty = config.LEVERAGE_PENALTY["vision_high_risk"]
            leverage += penalty
            penalties.append(f"Vision 고위험 ({penalty}x)")
            risk_factors.append("Vision AI 고위험 패턴")

        # 5. 연속 손실 3회+
        if ctx.recent_losses >= 3:
            penalty = config.LEVERAGE_PENALTY["consecutive_loss_3"]
            leverage += penalty
            penalties.append(f"연속 손실 {ctx.recent_losses}회 ({penalty}x)")
            risk_factors.append(f"연속 손실 {ctx.recent_losses}회")

        # 6. 공포탐욕지수 극단
        fg = market.fear_greed
        if fg < 20 or fg > 80:
            penalty = config.LEVERAGE_PENALTY["fear_greed_extreme"]
            leverage += penalty
            penalties.append(f"FGI 극단 {fg} ({penalty}x)")
            risk_factors.append(f"공포탐욕 극단 ({fg})")

        # 7. 포트폴리오 리스크 반영
        if risk.portfolio_risk == RiskLevel.CRITICAL:
            leverage = min(leverage, 10)
            risk_factors.append("포트폴리오 CRITICAL")
        elif risk.portfolio_risk == RiskLevel.HIGH:
            leverage = min(leverage, 12)
            risk_factors.append("포트폴리오 HIGH")

        # 범위 클램프
        leverage = max(config.LEVERAGE_MIN, min(config.LEVERAGE_MAX, leverage))

        # 현재 레버리지 (포지션에서 추출)
        current = config.LEVERAGE_DEFAULT
        if ctx.positions:
            current = ctx.positions[0].leverage

        # 이유 구성
        reason_parts = []
        if leverage < current:
            reason_parts.append(f"레버리지 {current}x → {leverage}x 하향 조정")
        elif leverage > current:
            reason_parts.append(f"레버리지 {current}x → {leverage}x 상향 조정")
        else:
            reason_parts.append(f"레버리지 {leverage}x 유지")

        if penalties:
            reason_parts.append("감산: " + ", ".join(penalties))

        return {
            "recommended_leverage": leverage,
            "current_leverage": current,
            "adjustment_reason": ". ".join(reason_parts),
            "risk_factors": risk_factors,
            "confidence": 0.75 if not risk_factors else 0.6,
        }
