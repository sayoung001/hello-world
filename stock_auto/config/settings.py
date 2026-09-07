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
    # 상관이 높은 지수를 한 그룹으로 묶는다. 그룹 안은 max(둘 중 나은 쪽),
    # 그룹 사이는 min(보수적 AND). 상관 지수를 각각 한 표로 세면 사실상
    # 같은 시장에 두 표를 줘서 게이트가 이유 없이 빡빡해진다.
    index_groups: tuple[tuple[str, ...], ...]
    # 일봉 스크리닝 사전필터 — 5일 평균 '일간' 거래대금 하한(시장 통화)
    daily_turnover_floor: float
    # 실시간 폭주 감지 — '윈도우 누적' 거래대금 하한(시장 통화). 척도가 다르다.
    surge_turnover_floor: float

    @property
    def turnover_floor(self) -> float:
        """구버전 호환 별칭 — 일봉 스크리닝 하한을 가리킨다."""
        return self.daily_turnover_floor


MARKET_CONFIG: dict[Market, MarketConfig] = {
    Market.US: MarketConfig(
        market=Market.US,
        currency="USD",
        # S&P500(FDR 'US500'), 나스닥 종합(IXIC), 나스닥100 ETF(QQQ)
        index_symbols=("US500", "IXIC", "QQQ"),
        index_groups=(("US500",), ("IXIC", "QQQ")),
        daily_turnover_floor=20_000_000,   # $20M/일 — 실행 가능한 유동성 하한
        surge_turnover_floor=2_000_000,    # $2M/윈도우
    ),
    Market.KR: MarketConfig(
        market=Market.KR,
        currency="KRW",
        index_symbols=("KS11", "KQ11"),   # KOSPI, KOSDAQ
        index_groups=(("KS11",), ("KQ11",)),
        daily_turnover_floor=15_000_000_000,   # 150억 원/일
        surge_turnover_floor=5_000_000_000,    # 50억 원/윈도우
    ),
}


def get_config(market: Market | str) -> MarketConfig:
    if isinstance(market, str):
        market = Market(market.upper())
    return MARKET_CONFIG[market]


# LLM 에이전트 분석 대상 상위 N (사용자 정책: 추천 종목만, ≤15)
TOP_N_FOR_AGENTS = 15
