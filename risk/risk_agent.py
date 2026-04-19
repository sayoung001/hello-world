"""
risk_agent.py — 포지션별 리스크 평가 에이전트
===============================================
각 포지션의 리스크 수준을 평가하고
SL/TP 조정, 사이징 변경을 제안.
AgentBase 상속.
"""

from __future__ import annotations
import json

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage

from risk.models import (
    PipelineContext, MarketAnalysis, RiskAssessment,
    PositionRiskAssessment, RiskLevel, PositionContext,
)
from risk import config


class RiskAgent(AgentBase):
    """
    포지션별 리스크 평가 + SL/TP/사이징 제안.

    역할:
    - 각 포지션의 청산 거리, PnL, 보유 시간 분석
    - 시장 분석 결과와 결합하여 리스크 레벨 판정
    - SL/TP 조정 및 사이징 배수 제안
    - 포트폴리오 전체 리스크 평가
    """

    def __init__(self):
        super().__init__(
            agent_id="risk",
            agent_name="리스크 관리자",
            role_description="포지션별 리스크를 평가하고 SL/TP/사이징을 제안하는 에이전트",
        )

    def _get_system_prompt(self) -> str:
        return (
            "당신은 20x 레버리지 암호화폐 선물 리스크 관리 전문가입니다. "
            "각 포지션의 리스크를 정량적으로 평가하고, "
            "SL/TP 조정 및 포지션 사이징을 제안합니다. "
            "20x 레버리지에서 5% 역행 시 청산됨을 항상 기억하세요. "
            "응답은 반드시 JSON 형식으로만 하세요."
        )

    def collect_data(self) -> dict:
        return {}

    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
        ctx = self._extract_context(context)
        market_analysis = self._extract_market_analysis(context)
        result = self._assess_positions(ctx, market_analysis)
        return self._build_message(
            data=result,
            confidence=result.get("confidence", 0.5),
            reasoning=result.get("reasoning", ""),
        )

    def assess(self, ctx: PipelineContext,
               market_analysis: MarketAnalysis) -> RiskAssessment:
        """파이프라인에서 직접 호출."""
        context = {
            "pipeline_context": ctx,
            "market_analysis": market_analysis,
        }
        msg = self.analyze({}, context)
        data = msg.data

        position_assessments = []
        for pa in data.get("position_assessments", []):
            position_assessments.append(PositionRiskAssessment(
                symbol=pa.get("symbol", ""),
                risk_level=RiskLevel(pa.get("risk_level", "medium")),
                sl_suggestion=float(pa.get("sl_suggestion", 0)),
                tp_suggestion=float(pa.get("tp_suggestion", 0)),
                sizing_mult=float(pa.get("sizing_mult", 1.0)),
                action=pa.get("action", "hold"),
                reasoning=pa.get("reasoning", ""),
            ))

        return RiskAssessment(
            portfolio_risk=RiskLevel(data.get("portfolio_risk", "medium")),
            position_assessments=position_assessments,
            max_drawdown_pct=float(data.get("max_drawdown_pct", 0)),
            total_risk_score=float(data.get("total_risk_score", 50)),
            reasoning=data.get("reasoning", ""),
            confidence=float(data.get("confidence", 0.5)),
        )

    def _extract_context(self, context: dict | None) -> PipelineContext:
        if context and "pipeline_context" in context:
            pc = context["pipeline_context"]
            return pc if isinstance(pc, PipelineContext) else PipelineContext(**pc)
        return PipelineContext()

    def _extract_market_analysis(self, context: dict | None) -> MarketAnalysis:
        if context and "market_analysis" in context:
            ma = context["market_analysis"]
            return ma if isinstance(ma, MarketAnalysis) else MarketAnalysis(**ma)
        return MarketAnalysis()

    def _assess_positions(self, ctx: PipelineContext,
                          market_analysis: MarketAnalysis) -> dict:
        """포지션별 + 포트폴리오 리스크 평가"""
        if not ctx.positions:
            return {
                "portfolio_risk": "low",
                "position_assessments": [],
                "max_drawdown_pct": 0,
                "total_risk_score": 10,
                "reasoning": "보유 포지션 없음",
                "confidence": 0.9,
            }

        assessments = []
        total_risk = 0.0
        worst_pnl = 0.0

        for pos in ctx.positions:
            pa = self._assess_single(pos, ctx.market_state, market_analysis)
            assessments.append(pa)
            total_risk += pa.get("risk_score_raw", 50)
            pnl = pos.pnl_pct
            if pnl < worst_pnl:
                worst_pnl = pnl

        avg_risk = total_risk / len(ctx.positions) if ctx.positions else 0
        # 포트폴리오 노출도 가산
        if ctx.total_exposure > 0 and ctx.account_balance > 0:
            exposure_ratio = ctx.total_exposure / ctx.account_balance
            avg_risk += min(20, exposure_ratio * 5)

        # 시장 분석 반영
        if market_analysis.volatility_regime == "extreme":
            avg_risk += 15
        elif market_analysis.volatility_regime == "high":
            avg_risk += 8

        avg_risk = min(100, avg_risk)

        if avg_risk >= config.RISK_SCORE_THRESHOLDS["critical"]:
            portfolio_risk = "critical"
        elif avg_risk >= config.RISK_SCORE_THRESHOLDS["high"]:
            portfolio_risk = "high"
        elif avg_risk >= config.RISK_SCORE_THRESHOLDS["medium"]:
            portfolio_risk = "medium"
        else:
            portfolio_risk = "low"

        # LLM 심층 분석 시도
        llm_result = self._llm_assess(ctx, market_analysis, assessments, avg_risk)
        if llm_result:
            return llm_result

        reasoning_parts = [f"포지션 {len(ctx.positions)}개 평가"]
        reasoning_parts.append(f"포트폴리오 리스크 점수: {avg_risk:.0f}/100")
        if market_analysis.key_risks:
            reasoning_parts.append(f"시장 리스크: {', '.join(market_analysis.key_risks[:2])}")

        return {
            "portfolio_risk": portfolio_risk,
            "position_assessments": assessments,
            "max_drawdown_pct": round(abs(worst_pnl) * 20, 2),  # 20x 반영
            "total_risk_score": round(avg_risk, 1),
            "reasoning": ". ".join(reasoning_parts),
            "confidence": 0.6,
        }

    def _assess_single(self, pos: PositionContext, market, market_analysis) -> dict:
        """단일 포지션 리스크 평가 (규칙 기반)"""
        risk_score = 30  # 기본

        # PnL 기반 리스크
        roe = pos.roe_pct
        if roe < -60:
            risk_score += 30
        elif roe < -30:
            risk_score += 20
        elif roe < -10:
            risk_score += 10
        elif roe > 100:
            risk_score += 5  # 이익 실현 리스크

        # 청산 거리
        if pos.entry_price > 0 and pos.current_price > 0:
            max_adverse = 100.0 / pos.leverage
            if pos.direction == "long":
                current_move = (pos.current_price - pos.entry_price) / pos.entry_price * 100
            else:
                current_move = (pos.entry_price - pos.current_price) / pos.entry_price * 100
            liq_dist = max_adverse + current_move
            if liq_dist < 1.5:
                risk_score += 35
            elif liq_dist < 2.5:
                risk_score += 20
            elif liq_dist < 3.5:
                risk_score += 10
        else:
            liq_dist = 5.0

        # 보유 시간 (장기 보유 리스크)
        if pos.hold_candles > 96 * 4:  # 4일 이상
            risk_score += 10
        elif pos.hold_candles > 96:    # 1일 이상
            risk_score += 5

        # 시장 방향과 포지션 방향 불일치
        mkt_sentiment = market_analysis.overall_sentiment
        if (pos.direction == "long" and mkt_sentiment == "bearish") or \
           (pos.direction == "short" and mkt_sentiment == "bullish"):
            risk_score += 15

        risk_score = min(100, risk_score)

        # 리스크 레벨 결정
        if risk_score >= config.RISK_SCORE_THRESHOLDS["critical"]:
            level = "critical"
            action = "close"
        elif risk_score >= config.RISK_SCORE_THRESHOLDS["high"]:
            level = "high"
            action = "reduce"
        elif risk_score >= config.RISK_SCORE_THRESHOLDS["medium"]:
            level = "medium"
            action = "tighten_sl"
        else:
            level = "low"
            action = "hold"

        # SL/TP 제안
        sl_suggestion = pos.stop_loss
        tp_suggestion = pos.take_profit
        sizing_mult = config.SIZING_DEFAULT

        if level in ("high", "critical"):
            sizing_mult = 0.5
        if risk_score >= 70:
            sizing_mult = max(config.SIZING_MIN, sizing_mult - 0.2)

        return {
            "symbol": pos.symbol,
            "risk_level": level,
            "sl_suggestion": round(sl_suggestion, 2),
            "tp_suggestion": round(tp_suggestion, 2),
            "sizing_mult": round(sizing_mult, 2),
            "action": action,
            "reasoning": (
                f"리스크 점수 {risk_score}/100, 청산거리 {liq_dist:.1f}%, "
                f"ROE {roe:+.1f}%, 보유 {pos.hold_candles}캔들"
            ),
            "risk_score_raw": risk_score,
        }

    def _llm_assess(self, ctx, market_analysis, rule_assessments, avg_risk) -> dict | None:
        """LLM 심층 리스크 분석"""
        try:
            parts = [
                "현재 포지션의 리스크를 평가하세요.\n",
                f"[시장 분석] {market_analysis.overall_sentiment}, "
                f"변동성: {market_analysis.volatility_regime}, "
                f"리스크: {', '.join(market_analysis.key_risks[:3])}",
            ]
            for a in rule_assessments:
                parts.append(
                    f"\n[{a['symbol']}] {a['risk_level']} — {a['reasoning']}"
                )
            parts.append(f"\n규칙 기반 평균 리스크 점수: {avg_risk:.0f}/100")
            parts.append(
                "\nJSON으로 응답:\n"
                '{"portfolio_risk": "low/medium/high/critical", '
                '"position_assessments": [{"symbol": "", "risk_level": "low/medium/high/critical", '
                '"sl_suggestion": 0.0, "tp_suggestion": 0.0, "sizing_mult": 1.0, '
                '"action": "hold/reduce/close/tighten_sl", "reasoning": ""}], '
                '"max_drawdown_pct": 0, "total_risk_score": 0, '
                '"reasoning": "", "confidence": 0.0~1.0}'
            )

            result = self.llm_json("\n".join(parts), deep=False,
                                    max_tokens=config.RISK_MAX_TOKENS)
            if result.get("parse_error"):
                return None
            if "portfolio_risk" not in result:
                return None
            return result

        except Exception as e:
            print(f"  [RiskAgent] LLM 분석 실패: {e}")
            return None
