"""
protocol.py — 에이전트 간 메시지 프로토콜
==========================================
모든 에이전트의 입출력을 표준화하는 데이터 구조 정의.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any

KST = timezone(timedelta(hours=9))


class RiskLevel(Enum):
    """20x 레버리지 기준 리스크 분류"""
    SAFE = "SAFE"           # 청산가까지 충분한 여유, 시장 환경 우호적
    CAUTION = "CAUTION"     # 하나 이상의 불확실성 존재, 포지션 축소 권고
    DANGER = "DANGER"       # 복수의 위험 신호, 즉시 청산 또는 대폭 축소 권고


class MarketRegime(Enum):
    """시장 상태"""
    RISK_ON = "risk-on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk-off"


class ActionType(Enum):
    """포지션 권고 액션"""
    HOLD = "hold"
    REDUCE_SIZE = "reduce_size"
    CLOSE = "close"
    ADD = "add"
    ADJUST_SL = "adjust_sl"
    ADJUST_TP = "adjust_tp"


@dataclass
class AgentMessage:
    """에이전트 간 통신 메시지"""
    agent_id: str               # 발신 에이전트 ID
    agent_name: str             # 에이전트 이름 (한국어)
    timestamp: str = ""         # KST 타임스탬프
    confidence: float = 0.0     # 분석 신뢰도 (0~1)
    data: dict = field(default_factory=dict)    # 구조화된 분석 결과
    reasoning: str = ""         # 자연어 추론 과정
    warnings: list[str] = field(default_factory=list)  # 경고 사항

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


@dataclass
class PositionInfo:
    """현재 보유 포지션 정보"""
    symbol: str
    direction: str          # "long" | "short"
    entry_price: float
    current_price: float = 0.0
    size: float = 0.0       # USDT 기준 포지션 크기
    leverage: int = 20
    pnl_pct: float = 0.0    # 미실현 PnL%
    roe_pct: float = 0.0    # ROE% (레버리지 반영)
    hold_candles: int = 0   # 보유 캔들 수 (15분봉)

    @property
    def liquidation_distance_pct(self) -> float:
        """청산가까지 거리 (%)"""
        # 20x → ~5% 역행 시 청산 (수수료/펀딩 제외 근사치)
        max_adverse = 100.0 / self.leverage
        if self.direction == "long":
            current_move = (self.current_price - self.entry_price) / self.entry_price * 100
        else:
            current_move = (self.entry_price - self.current_price) / self.entry_price * 100
        return max_adverse + current_move  # 남은 여유 %

    @property
    def risk_level(self) -> RiskLevel:
        """청산 거리 기반 리스크 분류"""
        dist = self.liquidation_distance_pct
        if dist > 3.0:
            return RiskLevel.SAFE
        elif dist > 1.5:
            return RiskLevel.CAUTION
        else:
            return RiskLevel.DANGER


@dataclass
class PositionVerdict:
    """포지션별 최종 판결"""
    symbol: str
    direction: str
    risk_level: RiskLevel
    action: ActionType
    confidence: float
    breakout_probability: float = 0.0
    hold_duration: str = ""         # 권장 홀딩 시간
    reasoning: str = ""
    dissent: str = ""               # 반대 의견 (토론에서 나온)


@dataclass
class MarketConsensus:
    """Market Context Layer의 종합 컨센서스"""
    overall_risk: RiskLevel
    market_regime: MarketRegime
    btc_bias: str = "neutral"       # bullish / neutral / bearish
    confidence: float = 0.0
    key_levels: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    agent_messages: list[AgentMessage] = field(default_factory=list)
    position_verdicts: list[PositionVerdict] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
