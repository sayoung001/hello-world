"""
config.py — 리스크 관리 파라미터 설정
=======================================
"""

# ── 레버리지 ──
LEVERAGE_MIN = 10
LEVERAGE_MAX = 20
LEVERAGE_DEFAULT = 20

# ATR% → 레버리지 매핑
LEVERAGE_ATR_MAP = {
    1.0: 20,    # ATR < 1.0% → 20x (저변동)
    2.0: 15,    # ATR 1.0~2.0% → 15x (보통)
    3.0: 12,    # ATR 2.0~3.0% → 12x (고변동)
    99.0: 10,   # ATR > 3.0% → 10x (극고변동)
}

# 추가 레버리지 감산 요인
LEVERAGE_PENALTY = {
    "btc_level_4_plus": -2,     # BTC 필터 레벨 4+
    "extreme_funding": -3,       # 펀딩레이트 극단값 (|rate| > 0.05%)
    "vision_high_risk": -2,      # Vision AI high risk
    "consecutive_loss_3": -3,    # 연속 손실 3회+
    "fear_greed_extreme": -2,    # 공포탐욕 < 20 또는 > 80
}

# ── 포지션 사이징 ──
SIZING_MIN = 0.3
SIZING_MAX = 1.5
SIZING_DEFAULT = 1.0

# ── 헷지 ──
HEDGE_MAX_SIZE_PCT = 50.0       # 최대 헷지 크기 (포지션의 50%)
HEDGE_MIN_POSITION_SIZE = 50.0  # 최소 헷지 대상 (USDT)

# ── 리스크 임계값 ──
RISK_SCORE_THRESHOLDS = {
    "low": 30,
    "medium": 50,
    "high": 70,
    "critical": 85,
}

# 청산 거리 경고 임계값 (%)
LIQUIDATION_WARNING_PCT = 2.5   # 20x 기준 2.5% 이하면 DANGER

# ── 드로다운 ──
MAX_PORTFOLIO_DRAWDOWN_PCT = 15.0  # 포트폴리오 최대 허용 드로다운

# ── LLM 설정 ──
ANALYST_MAX_TOKENS = 2000
RISK_MAX_TOKENS = 2500
HEDGE_MAX_TOKENS = 1500
LEVERAGE_MAX_TOKENS = 1000
