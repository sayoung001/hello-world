"""
시장별 구성요소 — 공통 코드가 시장 차이를 읽는 단 하나의 창구.

폴더:
  markets/__init__.py  이 파일. MarketSpec(시간표·표기·통화)와 조회 함수.
  markets/us/          미국장 전용
                         exchange.py          KIS 해외 실시간 거래소 코드(NAS/NYS/AMS)·회사명
                         earnings.py          실적 발표일 캘린더(게이트)
                         saveticker_store.py  세이브티커 뉴스 저장소(PC → git → VM)
                         news.py              미국 RSS 피드·구글뉴스 검색어
  markets/kr/          국내장 전용
                         universe.py          KRX 유니버스(시총 상위) + 업종 → 섹터 키/섹터 ETF
                         etf.py               추천 종목에 묶어 주는 관련 ETF(한국투자증권 거래 가능 = KRX 상장)
                         news.py              한국어 구글뉴스·연합뉴스 RSS

원칙:
  · screener / agents / tracking / notify 같은 공통 모듈은 `if market == KR` 분기를
    늘리지 않고 여기의 spec()·함수로 차이를 읽는다.
  · 한 시장에만 있는 기능(미국 실적 캘린더, 국내 관련 ETF)은 그 시장 폴더에만 둔다.
  · 시장을 하나 더 붙이려면 markets/<xx>/ 폴더와 SPECS 항목 하나를 추가한다.

운영 켜기: 미국장은 항상, 국내장은 .env 의 ENABLE_KR=1 일 때.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from stock_auto.config.settings import Market


@dataclass(frozen=True)
class MarketSpec:
    market: Market
    tz: str                        # 거래소 시간대(IANA)
    open_hm: tuple[int, int]       # 정규장 개장(현지)
    close_hm: tuple[int, int]      # 정규장 마감(현지)
    session_minutes: int           # 실시간 모니터 가동 시간
    batch_env: str                 # 일일 배치 시각 환경변수(KST HH:MM)
    batch_default: str             # 기본 배치 시각(KST)
    batch_days: str                # APScheduler day_of_week
    batch_note: str                # 왜 그 시각인가(로그·문서용)
    price_fmt: str                 # 가격 표기 — 원화는 소수점이 없다
    code_label: str                # '티커' / '종목코드'
    currency: str


SPECS: dict[Market, MarketSpec] = {
    Market.US: MarketSpec(
        market=Market.US, tz="America/New_York",
        open_hm=(9, 30), close_hm=(16, 0), session_minutes=390,
        batch_env="STOCK_BATCH_TIME_KST", batch_default="09:30", batch_days="tue-sat",
        batch_note="미국 월~금 장이 끝난 다음날 아침(KST). PC 가 밤사이 뉴스를 메운 뒤",
        price_fmt="{:,.2f}", code_label="티커", currency="USD"),
    Market.KR: MarketSpec(
        market=Market.KR, tz="Asia/Seoul",
        open_hm=(9, 0), close_hm=(15, 30), session_minutes=390,
        batch_env="STOCK_BATCH_TIME_KR_KST", batch_default="16:10", batch_days="mon-fri",
        batch_note="국내장 마감(15:30) 뒤 일봉이 확정된 다음 — 다음 날 개장용 추천",
        price_fmt="{:,.0f}", code_label="종목코드", currency="KRW"),
}


def spec(market: Market | str) -> MarketSpec:
    if isinstance(market, str):
        market = Market(market.upper())
    return SPECS[market]


def batch_time_kst(market: Market | str) -> tuple[int, int]:
    """배치 시각(KST). 환경변수가 잘못되면 기본값 + 경고."""
    s = spec(market)
    raw = os.environ.get(s.batch_env, s.batch_default).strip()
    try:
        h, m = raw.split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    print(f"[markets] ⚠️ {s.batch_env}={raw!r} 를 읽을 수 없어 {s.batch_default} 으로 둡니다")
    dh, dm = s.batch_default.split(":")
    return int(dh), int(dm)


def fmt_price(market: Market | str, v) -> str:
    """시장 통화 표기. 원화는 '72,500', 달러는 '182.50'."""
    try:
        if v is None or v == "":
            return "-"
        f = float(v)
        if f != f:
            return "-"
        return spec(market).price_fmt.format(f)
    except (TypeError, ValueError):
        return str(v)


def kr_enabled() -> bool:
    return os.environ.get("ENABLE_KR", "").strip().lower() in ("1", "true", "yes", "y")


def enabled_markets() -> tuple[Market, ...]:
    """미국장은 항상, 국내장은 ENABLE_KR=1 일 때."""
    return (Market.US, Market.KR) if kr_enabled() else (Market.US,)


def news_module(market: Market | str):
    """시장별 뉴스 소스 모듈(피드 URL·검색어). news_collector 가 쓴다."""
    m = Market(market.upper()) if isinstance(market, str) else market
    if m == Market.KR:
        from stock_auto.markets.kr import news as kr_news
        return kr_news
    from stock_auto.markets.us import news as us_news
    return us_news
