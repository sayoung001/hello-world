"""
enriched_state.py — 통합 상태 빌더
=====================================
Vision + RAG + Memory + 시장 상태를 결합하여 PipelineContext를 생성.
Risk Pipeline 입력 + RL state 입력 양쪽에 모두 활용 가능.

흐름:
  signal + v9_positions + market_data
      → EnrichedStateBuilder.build()
      → PipelineContext (risk pipeline 입력)
         + brain_advice (Memory 자문)
         + rag_features (RAG 수치 피처)
         + vision_features (Vision 수치 피처)
"""

from __future__ import annotations
import os
import time
from typing import Any

from risk.models import (
    PipelineContext, PositionContext, MarketState,
)


class EnrichedStateBuilder:
    """
    통합 상태 빌더 — Phase A+B+C 모듈을 하나로 조합.

    선택적 모듈:
      - vision_scorer: Vision 피처 (없으면 빈 dict)
      - retriever: RAG 검색 (없으면 빈 dict)
      - context_builder: RAG 컨텍스트 변환 (없으면 빈 텍스트)
      - brain: TradingBrain 자문 (없으면 빈 dict)

    외부 의존성 없이 빈 모듈만으로도 정상 작동 (규칙 기반 리스크만 실행).
    """

    def __init__(self,
                 vision_scorer=None,
                 retriever=None,
                 context_builder=None,
                 brain=None):
        self.vision_scorer = vision_scorer
        self.retriever = retriever
        self.context_builder = context_builder
        self.brain = brain

    # ─────────────────────────────────────
    # 1. 메인 빌더
    # ─────────────────────────────────────
    def build(self,
              signal: dict | None = None,
              positions: list | None = None,
              market_data: dict | None = None,
              vision_analysis: dict | None = None,
              account_balance: float = 0.0,
              recent_losses: int = 0) -> dict:
        """
        통합 상태 빌드.

        Args:
            signal: 시그널 dict (symbol, direction, gap, confidence, adx, ...)
            positions: v9 Position 객체 리스트 (또는 dict 리스트)
            market_data: BTC 가격/ATR/FGI/펀딩 등 시장 스냅샷
            vision_analysis: PatternAnalyzer.analyze() 결과 (선택)
            account_balance: 계정 잔고
            recent_losses: 연속 손실 횟수

        Returns:
            {
                "pipeline_context": PipelineContext,
                "brain_advice": dict,     # TradingBrain.consult() 결과
                "rag_features": dict,     # ContextBuilder.build_for_rl() 결과
                "vision_features": dict,  # VisionScorer.to_state_features() 결과
                "rag_text": str,          # ContextBuilder.build_for_llm() 결과
            }
        """
        signal = signal or {}
        positions = positions or []
        market_data = market_data or {}

        # 1. MarketState 구성
        market_state = self._build_market_state(market_data)

        # 2. Positions 변환 (v9 Position → PositionContext)
        pos_contexts = self._convert_positions(positions)

        # 3. Vision 피처
        vision_features = self._extract_vision_features(
            vision_analysis, signal.get("direction", "")
        )

        # 4. RAG 피처 + 텍스트
        rag_features, rag_text = self._extract_rag_context(signal)

        # 5. Memory 자문 (TradingBrain)
        brain_advice = self._consult_brain(signal, market_data)

        # 6. PipelineContext 완성
        total_exposure = sum(p.size_usdt for p in pos_contexts)
        ctx = PipelineContext(
            signal=signal,
            positions=pos_contexts,
            market_state=market_state,
            vision_features=vision_features,
            rag_context=rag_features,
            rag_text=rag_text,
            account_balance=account_balance,
            total_exposure=total_exposure,
            recent_losses=recent_losses,
        )

        return {
            "pipeline_context": ctx,
            "brain_advice": brain_advice,
            "rag_features": rag_features,
            "vision_features": vision_features,
            "rag_text": rag_text,
        }

    # ─────────────────────────────────────
    # 2. MarketState 변환
    # ─────────────────────────────────────
    def _build_market_state(self, md: dict) -> MarketState:
        """market_data dict → MarketState"""
        return MarketState(
            btc_price=float(md.get("btc_price", 0)),
            btc_change_1h=float(md.get("btc_change_1h", 0)),
            btc_change_4h=float(md.get("btc_change_4h", 0)),
            btc_change_24h=float(md.get("btc_change_24h", 0)),
            btc_level=int(md.get("btc_level", 0)),
            btc_rsi=float(md.get("btc_rsi", 50)),
            btc_atr_pct=float(md.get("btc_atr_pct", 0)),
            market_regime=str(md.get("market_regime", "neutral")),
            fear_greed=int(md.get("fear_greed", 50)),
            funding_rate=float(md.get("funding_rate", 0)),
            dominant_trend=str(md.get("dominant_trend", "neutral")),
        )

    # ─────────────────────────────────────
    # 3. Positions 변환
    # ─────────────────────────────────────
    def _convert_positions(self, positions: list) -> list[PositionContext]:
        """v9 Position 객체 (or dict) → PositionContext"""
        result = []
        for pos in positions:
            try:
                if isinstance(pos, dict):
                    result.append(self._dict_to_position(pos))
                else:
                    result.append(self._obj_to_position(pos))
            except Exception as e:
                print(f"  [EnrichedState] 포지션 변환 실패: {e}")
        return result

    def _dict_to_position(self, d: dict) -> PositionContext:
        """dict → PositionContext"""
        entry = float(d.get("entry_price", 0))
        current = float(d.get("current_price", entry))
        direction = d.get("direction", "long")
        leverage = int(d.get("leverage", 20))

        if entry and current:
            if direction == "long":
                pnl_pct = (current - entry) / entry * 100
            else:
                pnl_pct = (entry - current) / entry * 100
            roe_pct = pnl_pct * leverage
        else:
            pnl_pct = roe_pct = 0.0

        return PositionContext(
            symbol=d.get("symbol", ""),
            direction=direction,
            entry_price=entry,
            current_price=current,
            size_usdt=float(d.get("size_usdt", 0)),
            leverage=leverage,
            pnl_pct=pnl_pct,
            roe_pct=roe_pct,
            hold_candles=int(d.get("hold_candles", 0)),
            stop_loss=float(d.get("stop_loss", 0)),
            take_profit=float(d.get("take_profit", 0)),
            reasons=d.get("reasons", []),
        )

    def _obj_to_position(self, pos) -> PositionContext:
        """v9 Position 객체 → PositionContext"""
        entry = getattr(pos, "entry_price", 0)
        current = getattr(pos, "current_price", 0) or getattr(pos, "last_price", entry)
        direction = getattr(pos, "direction", "long")
        leverage = getattr(pos, "leverage", 20)
        quantity = getattr(pos, "quantity", 0)

        if entry and current:
            if direction == "long":
                pnl_pct = (current - entry) / entry * 100
            else:
                pnl_pct = (entry - current) / entry * 100
            roe_pct = pnl_pct * leverage
        else:
            pnl_pct = roe_pct = 0.0

        hold = 0
        if hasattr(pos, "hold_candles_15m"):
            try:
                hold = pos.hold_candles_15m()
            except Exception:
                hold = 0
        elif hasattr(pos, "hold_candles"):
            hold = int(getattr(pos, "hold_candles", 0))

        return PositionContext(
            symbol=getattr(pos, "symbol", ""),
            direction=direction,
            entry_price=entry,
            current_price=current,
            size_usdt=quantity * entry if quantity and entry else 0,
            leverage=leverage,
            pnl_pct=pnl_pct,
            roe_pct=roe_pct,
            hold_candles=hold,
            stop_loss=getattr(pos, "stop_loss", 0) or 0,
            take_profit=getattr(pos, "take_profit", 0) or 0,
            reasons=getattr(pos, "reasons", []) or [],
        )

    # ─────────────────────────────────────
    # 4. Vision 피처
    # ─────────────────────────────────────
    def _extract_vision_features(self, vision_analysis: dict | None,
                                  direction: str) -> dict:
        """Vision 분석 결과 → 수치 피처"""
        if not self.vision_scorer or not vision_analysis:
            return {}
        try:
            return self.vision_scorer.to_state_features(
                vision_analysis, signal_direction=direction
            )
        except Exception as e:
            print(f"  [EnrichedState] Vision 피처 추출 실패: {e}")
            return {}

    # ─────────────────────────────────────
    # 5. RAG 컨텍스트
    # ─────────────────────────────────────
    def _extract_rag_context(self, signal: dict) -> tuple[dict, str]:
        """RAG 검색 + 컨텍스트 변환"""
        if not self.retriever:
            return {}, ""

        try:
            similar_trades = self.retriever.retrieve_similar_trades(
                signal, top_k=5
            )
        except Exception as e:
            print(f"  [EnrichedState] RAG 거래 검색 실패: {e}")
            similar_trades = []

        try:
            similar_news = self.retriever.retrieve_similar_news(signal, top_k=3) \
                if hasattr(self.retriever, "retrieve_similar_news") else []
        except Exception:
            similar_news = []

        features = {}
        text = ""

        if self.context_builder:
            try:
                features = self.context_builder.build_for_rl(
                    similar_trades, similar_news
                )
                text = self.context_builder.build_for_llm(
                    similar_trades, similar_news
                )
            except Exception as e:
                print(f"  [EnrichedState] RAG 컨텍스트 변환 실패: {e}")

        return features, text

    # ─────────────────────────────────────
    # 6. Memory 자문
    # ─────────────────────────────────────
    def _consult_brain(self, signal: dict, market_data: dict) -> dict:
        """TradingBrain 자문"""
        if not self.brain:
            return {}

        try:
            advice = self.brain.consult(signal, market_state=market_data)
            return advice
        except Exception as e:
            print(f"  [EnrichedState] Brain 자문 실패: {e}")
            return {}

    # ─────────────────────────────────────
    # 7. 편의 메서드
    # ─────────────────────────────────────
    def summarize(self, enriched: dict) -> str:
        """통합 상태를 한 줄 요약"""
        ctx: PipelineContext = enriched["pipeline_context"]
        brain = enriched.get("brain_advice", {})
        rag = enriched.get("rag_features", {})
        vision = enriched.get("vision_features", {})

        parts = [
            f"BTC ${ctx.market_state.btc_price:,.0f} L{ctx.market_state.btc_level} "
            f"ATR {ctx.market_state.btc_atr_pct:.1f}%",
            f"포지션 {len(ctx.positions)}개 (노출 ${ctx.total_exposure:,.0f})",
        ]

        if brain:
            parts.append(
                f"Brain 신뢰도 {brain.get('brain_confidence', 0):.2f} "
                f"(규칙 {len(brain.get('applicable_rules', []))}개)"
            )

        if rag:
            parts.append(
                f"RAG 승률 {rag.get('similar_trade_winrate', 0):.0%} "
                f"(SL {rag.get('historical_sl_hit_rate', 0):.0%})"
            )

        if vision:
            parts.append(
                f"Vision 정합 {vision.get('vision_breakout_align', 0):+.1f} "
                f"(리스크 {vision.get('vision_risk_level', 0):.1f})"
            )

        return " | ".join(parts)
