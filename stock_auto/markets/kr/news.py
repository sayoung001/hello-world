"""
국내장 뉴스 소스 — 한국어 구글뉴스 RSS 와 연합뉴스 경제 RSS. 수집 로직은 agents/news_collector.

종목별 소스(STOCK_KR_NEWS_SOURCES, 기본 "google"):
  google   https://news.google.com/rss/search?q="<회사명>"&hl=ko&gl=KR&ceid=KR:ko
           종목코드(005930)로 검색하면 아무것도 안 나온다 — 회사명이 필수다.
           회사명은 markets/kr/universe 가 KRX 상장목록에서 받아 캐시한다.
           없으면 '<코드> 주가' 로 검색(품질 낮음).

시장 전체 헤드라인(STOCK_KR_MARKET_NEWS_SOURCES, 기본 "google_kr,yna_economy"):
  google_kr     '증시 OR 코스피 OR 코스닥' 최근 1일
  yna_economy   연합뉴스 경제 https://www.yna.co.kr/rss/economy.xml
"""

from __future__ import annotations

from typing import Callable
from urllib.parse import quote_plus

from stock_auto.config.settings import Market

MARKET = Market.KR

GOOGLE_RSS_KO = ("https://news.google.com/rss/search"
                 "?q={query}&hl=ko&gl=KR&ceid=KR:ko")

DEFAULT_SOURCES = "google"
SOURCES_ENV = "STOCK_KR_NEWS_SOURCES"

MARKET_FEEDS: dict[str, str] = {
    "google_kr": GOOGLE_RSS_KO.format(query=quote_plus("증시 OR 코스피 OR 코스닥 when:1d")),
    "yna_economy": "https://www.yna.co.kr/rss/economy.xml",
    "yna_market": "https://www.yna.co.kr/rss/market.xml",
}
DEFAULT_MARKET_SOURCES = "google_kr,yna_economy"
MARKET_SOURCES_ENV = "STOCK_KR_MARKET_NEWS_SOURCES"


def google_query(code: str, name: str = "") -> str:
    """검색어(URL 인코딩 전). 회사명이 있으면 '"삼성전자"', 없으면 '005930 주가'."""
    name = (name or "").strip()
    if name:
        return f'"{name}"'
    return f"{code} 주가"


def context(codes: list[str]) -> dict:
    """회사명 — markets/kr/universe 캐시."""
    try:
        from stock_auto.markets.kr import universe as kru
        return {"names": kru.load_names(codes)}
    except Exception:  # noqa: BLE001
        return {"names": {}}


def _google_url(code: str, ctx: dict) -> str:
    return GOOGLE_RSS_KO.format(query=quote_plus(google_query(code, ctx.get("names", {}).get(code, ""))))


TICKER_SOURCES: dict[str, Callable[[str, dict], str]] = {
    "google": _google_url,
}


def local_items(codes: list[str], days: int = 2, limit: int = 15) -> dict[str, list[dict]]:
    """국내장은 로컬 수집 소스가 없다."""
    return {}


def market_picks_text(days: int = 1, limit: int = 20) -> str:
    """국내장은 편집자 선정(PICK) 소스가 없다 — 공개 RSS 로 간다."""
    return ""
