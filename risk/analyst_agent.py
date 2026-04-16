"""
analyst_agent.py — 시장 상태 종합 분석 에이전트
=================================================
기존 6개 에이전트 출력 + Vision + RAG를 종합하여
시장 전체 센티먼트와 주요 리스크 요인을 분석.
AgentBase 상속.
"""

from __future__ import annotations
import json

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage

from risk.models import (
    PipelineContext, MarketAnalysis, MarketState,
)
from risk import config


class AnalystAgent(AgentBase):
    """
    시장 상태 종합 분석 에이전트.

    역할:
    - BTC 구조, 매크로, 뉴스 등 기존 에이전트 출력 종합
    - Vision AI 패턴 분석 결과 반영
    - RAG 유사 상황 컨텍스트 반영
    - 전체 시장 센티먼트/변동성 레짐 판단
    """

    def __init__(self):
        super().__init__(
            agent_id="analyst",
            agent_name="시장 분석가",
            role_description="BTC 구조, 매크로, 뉴스, Vision, RAG를 종합하여 시장 상태를 분석하는 에이전트",
        )

    def _get_system_prompt(self) -> str:
        return (
            "당신은 암호화폐 시장 분석 전문가입니다. "
            "BTC 가격 구조, 매크로 환경, 뉴스 센티먼트, 차트 패턴, "
            "과거 유사 상황을 종합하여 현재 시장 상태를 정확히 판단합니다. "
            "20x 레버리지 선물 트레이딩 관점에서 분석하세요. "
            "응답은 반드시 JSON 형식으로만 하세요."
        )

    def collect_data(self) -> dict:
        """파이프라인에서 PipelineContext를 직접 전달받으므로, 기본 구현은 빈 dict."""
        return {}

    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
        """
        PipelineContext 기반 시장 분석.

        context["pipeline_context"]에 PipelineContext가 전달됨.
        """
        ctx = self._extract_pipeline_context(context)
        market = ctx.market_state

        # 규칙 기반 초벌 분석 (LLM 없이)
        rule_analysis = self._rule_based_analysis(market, ctx)

        # LLM 심층 분석 시도
        llm_analysis = self._llm_analysis(market, ctx, rule_analysis)

        # 최종 결과 병합
        final = llm_analysis if llm_analysis else rule_analysis

        return self._build_message(
            data=final,
            confidence=final.get("confidence", 0.5),
            reasoning=final.get("reasoning", ""),
        )

    def analyze_market(self, ctx: PipelineContext) -> MarketAnalysis:
        """
        파이프라인에서 직접 호출하는 메서드.
        Returns: MarketAnalysis
        """
        context = {"pipeline_context": ctx}
        msg = self.analyze({}, context)
        data = msg.data

        return MarketAnalysis(
            overall_sentiment=data.get("overall_sentiment", "neutral"),
            sentiment_score=float(data.get("sentiment_score", 0)),
            volatility_regime=data.get("volatility_regime", "normal"),
            key_risks=data.get("key_risks", []),
            opportunities=data.get("opportunities", []),
            confidence=float(data.get("confidence", 0.5)),
            reasoning=data.get("reasoning", ""),
        )

    def _extract_pipeline_context(self, context: dict | None) -> PipelineContext:
        """context dict에서 PipelineContext 추출"""
        if context and "pipeline_context" in context:
            pc = context["pipeline_context"]
            if isinstance(pc, PipelineContext):
                return pc
            if isinstance(pc, dict):
                return PipelineContext(**pc)
        return PipelineContext()

    def _rule_based_analysis(self, market: MarketState, ctx: PipelineContext) -> dict:
        """규칙 기반 초벌 분석 (LLM 없이 빠르게)"""
        risks = []
        opportunities = []

        # BTC 레벨 기반
        if market.btc_level >= 4:
            risks.append(f"BTC 필터 레벨 {market.btc_level} — 고위험 구간")
        elif market.btc_level <= 1:
            opportunities.append("BTC 필터 레벨 안전 구간")

        # 변동성 판단
        atr = market.btc_atr_pct
        if atr > 3.0:
            vol_regime = "extreme"
            risks.append(f"극단적 변동성 (ATR {atr:.1f}%)")
        elif atr > 2.0:
            vol_regime = "high"
            risks.append(f"높은 변동성 (ATR {atr:.1f}%)")
        elif atr < 0.5:
            vol_regime = "low"
        else:
            vol_regime = "normal"

        # 공포탐욕지수
        fg = market.fear_greed
        if fg < 20:
            risks.append(f"극단적 공포 (FGI {fg})")
        elif fg > 80:
            risks.append(f"극단적 탐욕 (FGI {fg})")

        # 펀딩레이트
        if abs(market.funding_rate) > 0.05:
            risks.append(f"극단 펀딩레이트 ({market.funding_rate:.4f}%)")

        # RSI
        if market.btc_rsi > 75:
            risks.append(f"BTC RSI 과매수 ({market.btc_rsi:.0f})")
        elif market.btc_rsi < 25:
            risks.append(f"BTC RSI 과매도 ({market.btc_rsi:.0f})")

        # BTC 변동
        if market.btc_change_24h < -5:
            risks.append(f"BTC 24h 급락 ({market.btc_change_24h:+.1f}%)")
        elif market.btc_change_24h > 5:
            opportunities.append(f"BTC 24h 강세 ({market.btc_change_24h:+.1f}%)")

        # 포지션 노출도
        if ctx.total_exposure > 0 and ctx.account_balance > 0:
            exposure_ratio = ctx.total_exposure / ctx.account_balance
            if exposure_ratio > 3.0:
                risks.append(f"과도한 노출 (잔고 대비 {exposure_ratio:.1f}배)")

        # 센티먼트 판단
        sentiment_score = 0.0
        if market.btc_change_4h > 1:
            sentiment_score += 0.3
        elif market.btc_change_4h < -1:
            sentiment_score -= 0.3
        if market.dominant_trend == "bullish":
            sentiment_score += 0.2
        elif market.dominant_trend == "bearish":
            sentiment_score -= 0.2
        if fg < 30:
            sentiment_score -= 0.2
        elif fg > 70:
            sentiment_score += 0.2

        sentiment_score = max(-1.0, min(1.0, sentiment_score))

        if sentiment_score > 0.3:
            overall = "bullish"
        elif sentiment_score < -0.3:
            overall = "bearish"
        else:
            overall = "neutral"

        # Vision 반영
        vision = ctx.vision_features
        if vision and vision.get("vision_risk_level", 0) > 0.7:
            risks.append("Vision AI: 높은 차트 리스크 감지")

        # RAG 반영
        rag = ctx.rag_context
        if rag and rag.get("historical_sl_hit_rate", 0) > 0.6:
            risks.append(f"유사 상황 SL 히트율 높음 ({rag['historical_sl_hit_rate']:.0%})")

        confidence = 0.5
        if len(risks) == 0:
            confidence = 0.7
        elif len(risks) >= 3:
            confidence = 0.4

        reasoning = f"시장 센티먼트: {overall} ({sentiment_score:+.2f}), "
        reasoning += f"변동성: {vol_regime}, BTC 레벨: {market.btc_level}, "
        reasoning += f"FGI: {fg}, RSI: {market.btc_rsi:.0f}"

        return {
            "overall_sentiment": overall,
            "sentiment_score": round(sentiment_score, 4),
            "volatility_regime": vol_regime,
            "key_risks": risks,
            "opportunities": opportunities,
            "confidence": confidence,
            "reasoning": reasoning,
        }

    def _llm_analysis(self, market: MarketState, ctx: PipelineContext,
                      rule_result: dict) -> dict | None:
        """LLM 심층 분석 (API 키 있을 때만)"""
        try:
            prompt = self._build_prompt(market, ctx, rule_result)
            result = self.llm_json(prompt, deep=True, max_tokens=config.ANALYST_MAX_TOKENS)

            if result.get("parse_error"):
                return None

            # 필수 필드 검증
            required = ["overall_sentiment", "key_risks"]
            if not all(k in result for k in required):
                return None

            # confidence 보정
            if "confidence" not in result:
                result["confidence"] = rule_result.get("confidence", 0.5)

            return result

        except Exception as e:
            print(f"  [AnalystAgent] LLM 분석 실패, 규칙 기반 사용: {e}")
            return None

    def _build_prompt(self, market: MarketState, ctx: PipelineContext,
                      rule_result: dict) -> str:
        """LLM 프롬프트 구성"""
        parts = [
            "현재 암호화폐 시장 상태를 종합 분석하세요.\n",
            "[BTC 상태]",
            f"  가격: ${market.btc_price:,.0f}" if market.btc_price else "",
            f"  1H 변동: {market.btc_change_1h:+.2f}%, 4H: {market.btc_change_4h:+.2f}%, 24H: {market.btc_change_24h:+.2f}%",
            f"  RSI: {market.btc_rsi:.0f}, ATR%: {market.btc_atr_pct:.2f}%",
            f"  BTC 필터 레벨: {market.btc_level}/6",
            f"  추세: {market.dominant_trend}, 레짐: {market.market_regime}",
            f"\n[시장 지표]",
            f"  공포탐욕지수: {market.fear_greed}/100",
            f"  펀딩레이트: {market.funding_rate:.4f}%",
        ]

        if ctx.positions:
            parts.append(f"\n[보유 포지션: {len(ctx.positions)}개]")
            for p in ctx.positions:
                parts.append(
                    f"  {p.symbol} {p.direction} — ROE {p.roe_pct:+.1f}%, "
                    f"보유 {p.hold_candles}캔들"
                )

        if ctx.rag_text:
            parts.append(f"\n[RAG 유사 상황 분석]\n{ctx.rag_text[:500]}")

        if ctx.vision_features:
            vf = ctx.vision_features
            parts.append(f"\n[Vision AI]")
            parts.append(f"  추세: {vf.get('vision_trend', 0):.2f}, "
                         f"패턴 신뢰도: {vf.get('vision_pattern_conf', 0):.2f}, "
                         f"리스크: {vf.get('vision_risk_level', 0.5):.2f}")

        parts.append(f"\n[규칙 기반 초벌 분석]")
        parts.append(f"  센티먼트: {rule_result.get('overall_sentiment')}")
        parts.append(f"  주요 리스크: {', '.join(rule_result.get('key_risks', []))}")

        parts.append(
            "\n위 정보를 바탕으로 JSON으로 응답하세요:\n"
            '{"overall_sentiment": "bullish/neutral/bearish", '
            '"sentiment_score": -1.0~1.0, '
            '"volatility_regime": "low/normal/high/extreme", '
            '"key_risks": ["..."], '
            '"opportunities": ["..."], '
            '"confidence": 0.0~1.0, '
            '"reasoning": "분석 요약"}'
        )

        return "\n".join(p for p in parts if p)
