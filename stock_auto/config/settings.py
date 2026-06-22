"""
오케스트레이션 설정 — 시장 구분 · 지수 심볼 · 사전필터 · 매수 임계.

주의: 유동성 점수(통화별 center)와 전략 가중치/Penalty는 실제 소스
(US_KR_)CLAUD)의 indicators_v2.py / strategies.py 안에 그대로 들어있다.
여기서는 그 모듈들을 '오케스트레이션'하는 데 필요한 설정만 둔다.

★ 통화버그 수정의 본질은 indicators_v2.calculate_indicators(df, market=...)
  호출에서 market을 끝까지 전달하는 것이다(screener가 책임진다).
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
    # 매크로 레짐용 지수 심볼 (FinanceDataReader 기준)
    index_symbols: tuple[str, ...]
    # 스크리닝 유니버스 사전필터 — 5일 평균 거래대금 하한(시장 통화)
    turnover_floor: float


MARKET_CONFIG: dict[Market, MarketConfig] = {
    Market.US: MarketConfig(
        market=Market.US,
        currency="USD",
        index_symbols=("US500", "QQQ"),   # S&P500(FDR 'US500'), 나스닥100 ETF
        turnover_floor=2_000_000,         # $2M
    ),
    Market.KR: MarketConfig(
        market=Market.KR,
        currency="KRW",
        index_symbols=("KS11", "KQ11"),   # KOSPI, KOSDAQ
        turnover_floor=5_000_000_000,     # 50억 원
    ),
}


def get_config(market: Market | str) -> MarketConfig:
    if isinstance(market, str):
        market = Market(market.upper())
    return MARKET_CONFIG[market]


# LLM 에이전트 분석 대상 상위 N (사용자 정책: 추천 종목만, ≤15)
TOP_N_FOR_AGENTS = 15
