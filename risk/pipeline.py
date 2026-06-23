"""
pipeline.py — 리스크 관리 파이프라인
=======================================
Analyst → Risk → Hedge → Leverage 순차 실행.
AutoHedge 차용 멀티에이전트 파이프라인.
"""

from __future__ import annotations
import time
from datetime import datetime, timezone, timedelta

from risk.models import (
    PipelineContext, RiskDecision, MarketAnalysis,
    RiskAssessment, HedgeRecommendation, LeverageRecommendation,
    RiskLevel, Urgency,
)
from risk.analyst_agent import AnalystAgent
from risk.risk_agent import RiskAgent
from risk.hedge_agent import HedgeAgent
from risk.leverage_agent import LeverageAgent

KST = timezone(timedelta(hours=9))


class RiskPipeline:
    """
    리스크 분석 파이프라인.

    실행 트리거:
    1. 새 시그널 발생 시 (진입 전 리스크 점검)
    2. 보유 포지션의 15분 업데이트 시 (동적 리스크 관리)
    3. 급격한 시장 변동 감지 시 (긴급 리스크 점검)

    순서: Analyst → Risk → Hedge → Leverage
    """

    def __init__(self):
        self.analyst = AnalystAgent()
        self.risk_agent = RiskAgent()
        self.hedge_agent = HedgeAgent()
        self.leverage_agent = LeverageAgent()
        self._last_run: float = 0
        self._run_count: int = 0

    def evaluate(self, context: PipelineContext) -> RiskDecision:
        """
        전체 파이프라인 실행.

        Args:
            context: 파이프라인 입력 컨텍스트

        Returns:
            RiskDecision: 종합 리스크 판단 결과
        """
        start = time.time()
        errors = []

        # Step 1: Analyst — 시장 상태 종합 분석
        try:
            market_analysis = self.analyst.analyze_market(context)
        except Exception as e:
            print(f"  [Pipeline] Analyst 실패: {e}")
            errors.append(f"Analyst: {e}")
            market_analysis = MarketAnalysis(
                reasoning=f"분석 실패: {e}",
            )

        # Step 2: Risk — 포지션별 리스크 평가
        try:
            risk_assessment = self.risk_agent.assess(context, market_analysis)
        except Exception as e:
            print(f"  [Pipeline] Risk 실패: {e}")
            errors.append(f"Risk: {e}")
            risk_assessment = RiskAssessment(
                reasoning=f"평가 실패: {e}",
            )

        # Step 3: Hedge — 헷지 필요성 판단
        try:
            hedge_recommendation = self.hedge_agent.recommend(
                context, risk_assessment
            )
        except Exception as e:
            print(f"  [Pipeline] Hedge 실패: {e}")
            errors.append(f"Hedge: {e}")
            hedge_recommendation = HedgeRecommendation(
                reasoning=f"판단 실패: {e}",
            )

        # Step 4: Leverage — 레버리지 조절
        try:
            leverage_recommendation = self.leverage_agent.recommend(
                context, risk_assessment
            )
        except Exception as e:
            print(f"  [Pipeline] Leverage 실패: {e}")
            errors.append(f"Leverage: {e}")
            leverage_recommendation = LeverageRecommendation(
                adjustment_reason=f"조절 실패: {e}",
            )

        # 종합 리스크 레벨 결정
        overall_risk = self._determine_overall_risk(
            market_analysis, risk_assessment,
            hedge_recommendation, leverage_recommendation,
        )

        # 긴급도 결정
        urgency = self._determine_urgency(
            overall_risk, risk_assessment, hedge_recommendation,
        )

        # 종합 판단 근거
        reasoning = self._compile_reasoning(
            market_analysis, risk_assessment,
            hedge_recommendation, leverage_recommendation,
            errors,
        )

        elapsed = time.time() - start
        self._last_run = time.time()
        self._run_count += 1

        decision = RiskDecision(
            risk_level=overall_risk,
            market_analysis=market_analysis,
            risk_assessment=risk_assessment,
            hedge_recommendation=hedge_recommendation,
            leverage_recommendation=leverage_recommendation,
            reasoning=reasoning,
            urgency=urgency,
            timestamp=datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        )

        print(f"  [Pipeline] 완료 ({elapsed:.1f}초) — "
              f"리스크: {overall_risk.value}, 긴급도: {urgency.value}")

        return decision

    def evaluate_signal(self, signal: dict,
                        market_state: dict | None = None) -> RiskDecision:
        """
        새 시그널에 대한 리스크 평가 (진입 전).
        간편 호출용 래퍼.
        """
        from risk.models import MarketState
        ctx = PipelineContext(
            signal=signal,
            market_state=MarketState(**(market_state or {})),
        )
        return self.evaluate(ctx)

    def _determine_overall_risk(
        self, market: MarketAnalysis, risk: RiskAssessment,
        hedge: HedgeRecommendation, leverage: LeverageRecommendation,
    ) -> RiskLevel:
        """각 에이전트 결과를 종합하여 전체 리스크 레벨 결정"""
        risk_scores = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }

        scores = []

        # 포트폴리오 리스크
        scores.append(risk_scores.get(risk.portfolio_risk, 1))

        # 시장 변동성
        vol_map = {"low": 0, "normal": 1, "high": 2, "extreme": 3}
        scores.append(vol_map.get(market.volatility_regime, 1))

        # 헷지 긴급도
        urgency_map = {"low": 0, "medium": 1, "high": 2}
        scores.append(urgency_map.get(hedge.urgency.value, 0))

        # 레버리지 감소폭
        lev_diff = leverage.current_leverage - leverage.recommended_leverage
        if lev_diff >= 8:
            scores.append(3)
        elif lev_diff >= 5:
            scores.append(2)
        elif lev_diff >= 2:
            scores.append(1)
        else:
            scores.append(0)

        avg_score = sum(scores) / len(scores) if scores else 1

        if avg_score >= 2.5:
            return RiskLevel.CRITICAL
        elif avg_score >= 1.5:
            return RiskLevel.HIGH
        elif avg_score >= 0.8:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.LOW

    def _determine_urgency(
        self, overall_risk: RiskLevel, risk: RiskAssessment,
        hedge: HedgeRecommendation,
    ) -> Urgency:
        """긴급도 결정"""
        if overall_risk == RiskLevel.CRITICAL:
            return Urgency.HIGH
        if hedge.urgency == Urgency.HIGH:
            return Urgency.HIGH
        if overall_risk == RiskLevel.HIGH:
            return Urgency.MEDIUM
        if risk.total_risk_score >= 70:
            return Urgency.MEDIUM
        return Urgency.LOW

    def _compile_reasoning(
        self, market, risk, hedge, leverage, errors,
    ) -> str:
        """종합 판단 근거"""
        parts = []

        # 시장 분석 요약
        parts.append(
            f"시장: {market.overall_sentiment} "
            f"(변동성 {market.volatility_regime})"
        )

        # 리스크 요약
        if risk.position_assessments:
            parts.append(
                f"포지션 {len(risk.position_assessments)}개 "
                f"(점수 {risk.total_risk_score:.0f}/100)"
            )

        # 헷지 요약
        if hedge.action.value != "none":
            parts.append(f"헷지: {hedge.action.value} {hedge.size_pct:.0f}%")

        # 레버리지 요약
        if leverage.recommended_leverage != leverage.current_leverage:
            parts.append(
                f"레버리지: {leverage.current_leverage}x → "
                f"{leverage.recommended_leverage}x"
            )

        # 에러
        if errors:
            parts.append(f"경고: {', '.join(errors)}")

        return " | ".join(parts)

    @property
    def stats(self) -> dict:
        """파이프라인 통계"""
        return {
            "run_count": self._run_count,
            "last_run": self._last_run,
        }

    @staticmethod
    def format_telegram(decision: RiskDecision) -> str:
        """텔레그램 알림 메시지 포맷"""
        risk_emoji = {
            RiskLevel.LOW: "🟢",
            RiskLevel.MEDIUM: "🟡",
            RiskLevel.HIGH: "🟠",
            RiskLevel.CRITICAL: "🔴",
        }

        urgency_kr = {
            Urgency.LOW: "낮음",
            Urgency.MEDIUM: "보통",
            Urgency.HIGH: "긴급",
        }

        emoji = risk_emoji.get(decision.risk_level, "⚪")
        ma = decision.market_analysis
        ra = decision.risk_assessment
        hr = decision.hedge_recommendation
        lr = decision.leverage_recommendation

        lines = [
            f"{emoji} 리스크 파이프라인 결과",
            f"{'━' * 25}",
            f"종합 리스크: {decision.risk_level.value.upper()}",
            f"긴급도: {urgency_kr.get(decision.urgency, '보통')}",
            "",
            f"시장: {ma.overall_sentiment} ({ma.volatility_regime})",
        ]

        if ma.key_risks:
            lines.append(f"주요 리스크: {', '.join(ma.key_risks[:3])}")

        if ra.position_assessments:
            lines.append(f"\n포지션 리스크 (점수 {ra.total_risk_score:.0f}/100):")
            for pa in ra.position_assessments:
                level_emoji = risk_emoji.get(pa.risk_level, "⚪")
                lines.append(f"  {level_emoji} {pa.symbol}: {pa.action}")

        if hr.action.value != "none":
            lines.append(f"\n헷지: {hr.action.value} ({hr.size_pct:.0f}%)")
            lines.append(f"  사유: {hr.reasoning[:80]}")

        if lr.recommended_leverage != lr.current_leverage:
            lines.append(
                f"\n레버리지: {lr.current_leverage}x → {lr.recommended_leverage}x"
            )

        lines.extend([
            f"\n{'━' * 25}",
            f"{decision.timestamp}",
        ])

        return "\n".join(lines)
