"""
미국장 뉴스 소스 — RSS 피드 URL 과 구글뉴스 검색어. 수집 로직은 agents/news_collector.

종목별 소스(STOCK_NEWS_SOURCES, 기본 "saveticker,yahoo,google"):
  saveticker  PC 확장이 git 저장소에 올린 로컬 데이터(markets/us/saveticker_store) — 종목 태그 정확
  yahoo       https://feeds.finance.yahoo.com/rss/2.0/headline?s=<T>   (클라우드 IP 는 429 가 잦다)
  google      https://news.google.com/rss/search?q="<회사명>" OR "<거래소>: <T>"
              회사명·거래소는 markets/us/exchange 캐시에서. 없으면 '<T> stock'.

시장 전체 헤드라인(STOCK_MARKET_NEWS_SOURCES): 세이브티커 SAVE PICK 이 없을 때의 대체.
"""

from __future__ import annotations

from typing import Callable, Optional
from urllib.parse import quote_plus

from stock_auto.config.settings import Market

MARKET = Market.US

YAHOO_RSS = ("https://feeds.finance.yahoo.com/rss/2.0/headline"
             "?s={ticker}&region=US&lang=en-US")
GOOGLE_RSS = ("https://news.google.com/rss/search"
              "?q={query}&hl=en-US&gl=US&ceid=US:en")

DEFAULT_SOURCES = "saveticker,yahoo,google"
SOURCES_ENV = "STOCK_NEWS_SOURCES"

MARKET_FEEDS: dict[str, str] = {
    "cnbc": "https://www.cnbc.com/id/100003114/device/rss/rss.html",       # Top News
    "cnbc_finance": "https://www.cnbc.com/id/10000664/device/rss/rss.html",  # Finance
    "marketwatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "yahoo_market": "https://finance.yahoo.com/news/rssindex",
    "google_market": ("https://news.google.com/rss/search"
                      "?q=stock+market+when:1d&hl=en-US&gl=US&ceid=US:en"),
}
DEFAULT_MARKET_SOURCES = "cnbc,marketwatch,yahoo_market"
MARKET_SOURCES_ENV = "STOCK_MARKET_NEWS_SOURCES"

_EXCH_WORD = {"NAS": "NASDAQ", "NYS": "NYSE", "AMS": "NYSE American"}


def google_query(ticker: str, name: str = "", exch: str = "") -> str:
    """구글뉴스 검색어(URL 인코딩 전).

    'IT stock' 은 정보기술 기사 90건을 받아 온다 — Gartner 와 무관하다.
    회사명이 있으면 회사명으로, 보도자료 표기("NYSE: IT")도 함께 건다.
    회사명이 없으면 예전처럼 '<티커> stock'. 짧은 티커의 한계는 남는다.
    """
    t = ticker.upper().strip()
    parts = []
    if name:
        parts.append(f'"{name}"')
    ex = _EXCH_WORD.get((exch or "").upper())
    if ex:
        parts.append(f'"{ex}: {t}"')
    if not parts:
        return f"{t} stock"
    return " OR ".join(parts)


def context(tickers: list[str]) -> dict:
    """검색어 정밀도용 부가 정보 — 회사명·거래소. 캐시가 없으면 빈 dict."""
    try:
        from stock_auto.markets.us import exchange
        return {"names": exchange.load_names(tickers), "exch": exchange.load_map(tickers)}
    except Exception:  # noqa: BLE001
        return {"names": {}, "exch": {}}


def _yahoo_url(ticker: str, ctx: dict) -> str:
    return YAHOO_RSS.format(ticker=ticker)


def _google_url(ticker: str, ctx: dict) -> str:
    t = ticker.upper()
    q = google_query(t, ctx.get("names", {}).get(t, ""), ctx.get("exch", {}).get(t, ""))
    return GOOGLE_RSS.format(query=quote_plus(q))


# 소스 이름 → URL 빌더(ticker, context). 여기 없는 이름은 로컬 소스(saveticker)거나 무시.
TICKER_SOURCES: dict[str, Callable[[str, dict], str]] = {
    "yahoo": _yahoo_url,
    "google": _google_url,
}


def local_items(tickers: list[str], days: int = 2, limit: int = 15) -> dict[str, list[dict]]:
    """VM 이 직접 받지 않는 소스 — 세이브티커 로컬 저장소. {ticker: [item dict]}"""
    from stock_auto.markets.us import saveticker_store as SS
    out: dict[str, list[dict]] = {}
    for t, its in SS.items_for(tickers, days=days).items():
        out[t] = []
        for it in its[:limit]:
            title = (it.title or "").replace("[SAVE PICK]", "").strip() or (it.title_en or "").strip()
            out[t].append({"headline": title, "url": it.url,
                           "source": "SAVE PICK" if it.is_pick else (it.source or "saveticker"),
                           "published": it.kst_date})
    return out


def market_picks_text(days: int = 1, limit: int = 20) -> str:
    """오늘의 SAVE PICK(편집자 선정 주요뉴스)을 종목 무관하게 한 덩어리로.
    없으면 빈 문자열 — 호출측이 공개 RSS 로 대체한다."""
    try:
        from stock_auto.markets.us import saveticker_store as SS
        items = SS.picks(days=days)[:limit]
    except Exception:  # noqa: BLE001
        return ""
    lines = []
    for it in items:
        title = (it.title or "").replace("[SAVE PICK]", "").strip() or (it.title_en or "").strip()
        tk = f" [{' '.join(it.tickers[:4])}]" if it.tickers else ""
        lines.append(f"- {title}{tk} ({it.source or 'saveticker'}, {it.kst_date})")
    return "\n".join(lines)
