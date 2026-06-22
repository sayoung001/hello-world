"""
시장별 설정 — 통화·임계값·유동성 기준.

★ 통화 버그 수정의 핵심 (분석 §3.1):
  기존 US_KR_)CLAUD는 _calc_liquidity_score를 항상 'US' 기준으로 호출 →
  한국 종목(원화 거래대금)이 USD center와 비교돼 거의 전부 포화(≈1.4).
  여기서는 시장별 통화·center를 명시 분리하고, 모든 계산에 market 인자를 끝까지 전달한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Market(str, Enum):
    KR = "KR"   # KOSPI / KOSDAQ
    US = "US"   # NASDAQ / NYSE


@dataclass(frozen=True)
class MarketConfig:
    market: Market
    currency: str
    # 유동성 sigmoid 중심값(거래대금, 시장 통화 기준). 이 값 근처에서 score≈0.85.
    liquidity_center: float
    liquidity_scale: float          # log 공간 스케일(완만함 조절)
    # 시장별 기술 임계 프로파일 (분석 §3.14: 미·한 동일 임계는 부정확)
    rsi_oversold: float
    rvol_strong: float              # 강한 상대거래량 기준(일봉)
    # 지수 심볼 (매크로 레짐용, 분석 §3.7)
    index_symbols: tuple[str, ...]
    # 거래대금 하한(스크리닝 유니버스 사전 필터)
    turnover_floor: float


MARKET_CONFIG: dict[Market, MarketConfig] = {
    Market.US: MarketConfig(
        market=Market.US,
        currency="USD",
        liquidity_center=50_000_000,     # $50M 일거래대금 (분석 §3.1의 'USD center=5천만')
        liquidity_scale=1.2,
        rsi_oversold=30.0,
        rvol_strong=2.0,
        index_symbols=("SPY", "QQQ"),
        turnover_floor=2_000_000,        # $2M
    ),
    Market.KR: MarketConfig(
        market=Market.KR,
        currency="KRW",
        liquidity_center=5_000_000_000,  # 50억 원 일거래대금 (KR 현실 기준)
        liquidity_scale=1.2,
        rsi_oversold=30.0,
        rvol_strong=2.0,
        index_symbols=("KS11", "KQ11"),  # KOSPI, KOSDAQ (FinanceDataReader 심볼)
        turnover_floor=5_000_000_000,    # 50억 원
    ),
}


def get_config(market: Market | str) -> MarketConfig:
    if isinstance(market, str):
        market = Market(market.upper())
    return MARKET_CONFIG[market]


# 전략 가중치 (분석 문서 명시값 — 백테스트 전 가정치, 추후 검증)
WEIGHTS = {
    "money_strong": 2.0,
    "money_mid": 1.5,
    "money_weak": 1.0,
    "price_strong": 2.0,
    "price_mid": 1.5,
    "price_weak": 1.0,
}

# 매수 임계 (분석 문서 명시값)
BUY_EFFECTIVE_MIN = 4.0          # Effective ≥ 4.0
BUY_EFFECTIVE_ALT = 2.5          # 또는 ≥2.5 & Money≥1.5
BUY_MONEY_MIN_ALT = 1.5

# VAI 컷 (분석 문서: 1.5/1.2/2.0/1.1)
VAI_BREAKOUT = 1.5               # 돌파성 거래량
VAI_SUSTAIN = 1.2               # 지속 기준
VAI_SPIKE = 2.0                 # 단발 펌핑 의심(페널티 트리거)
VAI_MILD = 1.1
