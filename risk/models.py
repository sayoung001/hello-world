"""
models.py — 리스크 파이프라인 데이터 모델
==========================================
Pydantic 기반 구조화된 입출력 정의.
"""

from __future__ import annotations
from enum import Enum
from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HedgeAction(str, Enum):
    NONE = "none"
    PARTIAL_CLOSE = "partial_close"
    OPPOSITE_POSITION = "opposite_position"
    CORRELATED_HEDGE = "correlated_hedge"
    FULL_EXIT = "full_exit"


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ── 파이프라인 입력 ──

class PositionContext(BaseModel):
    """포지션 정보"""
    symbol: str = ""
    direction: str = ""           # "long" | "short"
    entry_price: float = 0.0
    current_price: float = 0.0
    size_usdt: float = 0.0
    leverage: int = 20
    pnl_pct: float = 0.0
    roe_pct: float = 0.0
    hold_candles: int = 0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class MarketState(BaseModel):
    """시장 상태 스냅샷"""
    btc_price: float = 0.0
    btc_change_1h: float = 0.0
    btc_change_4h: float = 0.0
    btc_change_24h: float = 0.0
    btc_level: int = 0              # BTC 필터 레벨 (0~6)
    btc_rsi: float = 50.0
    btc_atr_pct: float = 0.0        # ATR% (변동성)
    market_regime: str = "neutral"   # risk-on / neutral / risk-off
    fear_greed: int = 50             # 공포탐욕지수 (0~100)
    funding_rate: float = 0.0
    dominant_trend: str = "neutral"  # bullish / neutral / bearish


class PipelineContext(BaseModel):
    """파이프라인 입력 컨텍스트"""
    signal: dict = Field(default_factory=dict)
    positions: list[PositionContext] = Field(default_factory=list)
    market_state: MarketState = Field(default_factory=MarketState)
    vision_features: dict = Field(default_factory=dict)
    rag_context: dict = Field(default_factory=dict)
    rag_text: str = ""
    account_balance: float = 0.0
    total_exposure: float = 0.0    # 전체 포지션 USDT 합
    recent_losses: int = 0          # 최근 연속 손실 횟수


# ── 파이프라인 중간 결과 ──

class MarketAnalysis(BaseModel):
    """Analyst Agent 출력"""
    overall_sentiment: str = "neutral"   # bullish / neutral / bearish
    sentiment_score: float = 0.0         # -1.0 ~ 1.0
    volatility_regime: str = "normal"    # low / normal / high / extreme
    key_risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    reasoning: str = ""


class PositionRiskAssessment(BaseModel):
    """Risk Agent의 개별 포지션 평가"""
    symbol: str = ""
    risk_level: RiskLevel = RiskLevel.MEDIUM
    sl_suggestion: float = 0.0      # 제안 SL가
    tp_suggestion: float = 0.0      # 제안 TP가
    sizing_mult: float = 1.0        # 사이징 배수 (0.3~1.5)
    action: str = "hold"            # hold / reduce / close / tighten_sl
    reasoning: str = ""


class RiskAssessment(BaseModel):
    """Risk Agent 전체 출력"""
    portfolio_risk: RiskLevel = RiskLevel.MEDIUM
    position_assessments: list[PositionRiskAssessment] = Field(default_factory=list)
    max_drawdown_pct: float = 0.0
    total_risk_score: float = 50.0   # 0~100
    reasoning: str = ""
    confidence: float = 0.5


class HedgeRecommendation(BaseModel):
    """Hedge Agent 출력"""
    action: HedgeAction = HedgeAction.NONE
    size_pct: float = 0.0           # 헷지 크기 (포지션 대비 %)
    hedge_symbol: str = ""          # 상관 코인 헷지 시 심볼
    urgency: Urgency = Urgency.LOW
    exit_condition: str = ""        # 헷지 해제 조건
    reasoning: str = ""
    confidence: float = 0.5


class LeverageRecommendation(BaseModel):
    """Leverage Agent 출력"""
    recommended_leverage: int = 20   # 10~20
    current_leverage: int = 20
    adjustment_reason: str = ""
    risk_factors: list[str] = Field(default_factory=list)
    confidence: float = 0.5


# ── 파이프라인 최종 출력 ──

class RiskDecision(BaseModel):
    """리스크 파이프라인의 최종 출력"""
    risk_level: RiskLevel = RiskLevel.MEDIUM
    market_analysis: MarketAnalysis = Field(default_factory=MarketAnalysis)
    risk_assessment: RiskAssessment = Field(default_factory=RiskAssessment)
    hedge_recommendation: HedgeRecommendation = Field(default_factory=HedgeRecommendation)
    leverage_recommendation: LeverageRecommendation = Field(default_factory=LeverageRecommendation)
    reasoning: str = ""
    urgency: Urgency = Urgency.LOW
    timestamp: str = ""
