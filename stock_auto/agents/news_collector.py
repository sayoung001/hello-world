"""
뉴스 수집 — 시장별 소스 체인(markets.<us|kr>.news)에서 받아 종목별로 합친다.

이 모듈은 **수집 로직만** 가진다(HTTP·RSS 파싱·중복 제거·소스 차단기). 어떤 피드를
어떤 검색어로 치는지는 시장 모듈이 정한다:
  markets/us/news.py  야후·구글뉴스(en)·세이브티커(로컬) + CNBC·MarketWatch 시장 헤드라인
  markets/kr/news.py  구글뉴스(ko, 회사명) + 연합뉴스 경제 RSS

소스 차단기: 한 소스에서 429/403 이 연속 BREAKER_AFTER 번이면 이번 배치는 그 소스를
접고 다음 소스로 간다(남은 종목마다 두드려 로그만 늘리지 않는다).

시장 전체 헤드라인(PM 의 시장 맥락): 시장 모듈의 market_picks_text(세이브티커 PICK)
→ 없으면 공개 RSS(MARKET_FEEDS).

네트워크 없는 환경에서는 fetch 인자를 바꿔 끼워 체인 로직을 검증한다.
"""

from __future__ import annotations

import html
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Callable, Optional

from stock_auto.config.settings import Market
from stock_auto.markets import news_module

# ── 미국 소스 상수 재노출(호환) — 새 코드는 markets.us.news 를 직접 본다 ──
from stock_auto.markets.us.news import (  # noqa: F401
    YAHOO_RSS, GOOGLE_RSS, MARKET_FEEDS, DEFAULT_SOURCES, DEFAULT_MARKET_SOURCES,
    google_query, market_picks_text)

SAVETICKER_NEWS_URL = "https://www.saveticker.com/news"
MAX_PER_TICKER = 15          # NewsAgent 가 news[:15] 만 쓴다
REQUEST_GAP_SEC = 0.3        # 종목별 연속 요청 사이 간격(예의상 rate limit)
MARKET_HEADLINE_LIMIT = 20

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
}


@dataclass
class NewsItem:
    ticker: str
    headline: str
    url: str = ""
    source: str = ""
    published: str = ""          # ISO 날짜(파싱 가능할 때) 또는 원문 문자열


# ──────────────────────────────────────────────────────────────────────────
# HTTP
# ──────────────────────────────────────────────────────────────────────────
# 마지막 실패의 HTTP 상태 — 소스 차단기가 429/403 을 구분하는 데 쓴다.
LAST_FAIL: dict[str, Optional[int]] = {"status": None}
# 같은 소스에서 연속 이만큼 429/403 이면 이번 배치는 그 소스를 접는다.
BREAKER_AFTER = 2


def fetch_text(url: str, timeout: int = 10) -> Optional[str]:
    """GET → 본문. 실패는 사유를 찍고 None (조용한 실패 금지)."""
    LAST_FAIL["status"] = None
    try:
        import requests
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001
        resp = getattr(e, "response", None)
        LAST_FAIL["status"] = getattr(resp, "status_code", None)
        print(f"[news_collector] fetch 실패 {url[:60]}…: {type(e).__name__}: "
              f"{str(e)[:120]}")
        return None


def fetch_html(url: str = SAVETICKER_NEWS_URL, timeout: int = 10) -> Optional[str]:
    """(호환) saveticker 페이지 HTML."""
    return fetch_text(url, timeout)


# ──────────────────────────────────────────────────────────────────────────
# RSS 파서 — 표준 라이브러리만
# ──────────────────────────────────────────────────────────────────────────
def _iso(pub: str) -> str:
    """RFC-2822 pubDate → 'YYYY-MM-DD'. 못 읽으면 원문 그대로."""
    try:
        return parsedate_to_datetime(pub).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return (pub or "").strip()


def parse_rss(xml_text: str, ticker: str, default_source: str) -> list[NewsItem]:
    """RSS 2.0 <item> → NewsItem. 구글뉴스는 <source> 에 매체명이 있다."""
    import xml.etree.ElementTree as ET
    out: list[NewsItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[news_collector] RSS 파싱 실패({default_source}/{ticker}): {e}")
        return out
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        if len(title) < 8:
            continue
        link = (item.findtext("link") or "").strip()
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else default_source
        # 구글뉴스는 제목 끝에 " - 매체명" 을 붙인다. 중복 제거를 위해 떼어낸다.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")].rstrip()
        pub = item.findtext("pubDate") or ""
        out.append(NewsItem(ticker=ticker.upper(), headline=title, url=link,
                            source=source or default_source, published=_iso(pub)))
    return out


# ──────────────────────────────────────────────────────────────────────────
# saveticker HTML 파서 — 호환용으로만 남긴다
# ──────────────────────────────────────────────────────────────────────────
def parse_html(html_text: str) -> list[NewsItem]:
    """HTML → NewsItem. bs4 있으면 사용, 없으면 정규식 폴백. 티커는 추측이다."""
    items: list[NewsItem] = []
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html_text, "html.parser")
        for a in soup.find_all("a"):
            text = (a.get_text() or "").strip()
            href = a.get("href", "")
            if len(text) < 25:
                continue
            ticker = _guess_ticker(text) or _guess_ticker(href)
            items.append(NewsItem(ticker=ticker or "", headline=html.unescape(text),
                                  url=href, source="saveticker"))
    except ImportError:
        for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                             html_text, re.DOTALL | re.IGNORECASE):
            href, raw = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
            text = html.unescape(raw)
            if len(text) < 25:
                continue
            ticker = _guess_ticker(text) or _guess_ticker(href)
            items.append(NewsItem(ticker=ticker or "", headline=text, url=href,
                                  source="saveticker"))
    return items


def _guess_ticker(text: str) -> Optional[str]:
    """$AAPL / (AAPL) 형태만 — 보수적."""
    if not text:
        return None
    m = re.search(r"\$([A-Z]{1,5})\b", text) or re.search(r"\(([A-Z]{1,5})\)", text)
    return m.group(1) if m else None


def _match_saveticker(all_items: list[NewsItem], tickers: list[str]
                      ) -> dict[str, list[NewsItem]]:
    wanted = {t.upper() for t in tickers}
    out: dict[str, list[NewsItem]] = {t: [] for t in tickers}
    for it in all_items:
        tk = it.ticker.upper()
        if tk in wanted:
            out[tk].append(it)
            continue
        for t in tickers:
            if re.search(rf"\b{re.escape(t)}\b", it.headline, re.IGNORECASE):
                out[t].append(it)
                break
    return out


# ──────────────────────────────────────────────────────────────────────────
# 소스 체인
# ──────────────────────────────────────────────────────────────────────────
def _norm(h: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", " ", h.lower()).strip()


def _dedup_recent(items: list[NewsItem], limit: int = MAX_PER_TICKER) -> list[NewsItem]:
    """제목 중복 제거 후 최신순으로 limit 개. 날짜 없는 건 뒤로."""
    seen: set[str] = set()
    uniq: list[NewsItem] = []
    for it in items:
        k = _norm(it.headline)
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    uniq.sort(key=lambda x: x.published if re.match(r"\d{4}-\d{2}-\d{2}", x.published) else "",
              reverse=True)
    return uniq[:limit]


def _mkt(market) -> Market:
    return Market(market.upper()) if isinstance(market, str) else (market or Market.US)


def sources_from_env(market: Market | str = Market.US) -> list[str]:
    mod = news_module(_mkt(market))
    raw = os.environ.get(mod.SOURCES_ENV, mod.DEFAULT_SOURCES)
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def market_sources_from_env(market: Market | str = Market.US) -> list[str]:
    mod = news_module(_mkt(market))
    raw = os.environ.get(mod.MARKET_SOURCES_ENV, mod.DEFAULT_MARKET_SOURCES)
    return [s.strip().lower() for s in raw.split(",")
            if s.strip() and s.strip().lower() in mod.MARKET_FEEDS]


def collect_for_tickers(tickers: list[str],
                        html_text: Optional[str] = None,
                        fetch: Callable[[str], Optional[str]] = fetch_text,
                        sources: Optional[list[str]] = None,
                        market: Market | str = Market.US,
                        ) -> dict[str, list[NewsItem]]:
    """
    종목별 뉴스. 시장 모듈의 소스 체인을 순서대로 시도하고 결과를 합친다.

    html_text 를 주면 (호환·테스트용) saveticker 파서만 돌린다.
    fetch 를 바꿔 끼우면 네트워크 없이 체인 로직을 검증할 수 있다.
    """
    tickers = [t for t in tickers if t]
    if html_text is not None:
        return _match_saveticker(parse_html(html_text), tickers)

    m = _mkt(market)
    mod = news_module(m)
    chain = sources if sources is not None else sources_from_env(m)
    out: dict[str, list[NewsItem]] = {t: [] for t in tickers}
    stats: dict[str, int] = {}
    skipped: dict[str, str] = {}

    # 검색어 정밀도용 부가 정보(회사명·거래소). 캐시가 없으면 빈 dict.
    try:
        ctx = mod.context(tickers) or {}
    except Exception:  # noqa: BLE001
        ctx = {}
    names = ctx.get("names", {}) or {}

    # 종목별 원격 소스
    for src in chain:
        builder = mod.TICKER_SOURCES.get(src)
        if builder is None:
            continue
        got = 0
        consecutive = 0
        for i, t in enumerate(tickers):
            if i:
                time.sleep(REQUEST_GAP_SEC)
            body = fetch(builder(t, ctx))
            if not body:
                if LAST_FAIL.get("status") in (429, 403):
                    consecutive += 1
                    if consecutive >= BREAKER_AFTER:
                        skipped[src] = (f"{LAST_FAIL['status']} 연속 — 나머지 "
                                        f"{len(tickers) - i - 1}종목 생략")
                        break
                continue
            consecutive = 0
            items = parse_rss(body, t, src)
            out[t].extend(items)
            got += len(items)
        stats[src] = got

    # 로컬 소스(미국: 세이브티커 저장소) — 체인에 이름이 있을 때만
    local_names = [s for s in chain if s not in mod.TICKER_SOURCES]
    if local_names:
        try:
            got = 0
            for t, its in (mod.local_items(tickers) or {}).items():
                for d in its:
                    out.setdefault(t, []).append(NewsItem(
                        ticker=t, headline=d.get("headline", ""), url=d.get("url", ""),
                        source=d.get("source", ""), published=d.get("published", "")))
                    got += 1
            stats[local_names[0]] = got
        except Exception as e:  # noqa: BLE001 — 로컬 저장소가 없어도 체인은 간다
            print(f"[news_collector] 로컬 소스 읽기 실패: {type(e).__name__}: {e}")
            stats[local_names[0]] = 0

    for t in tickers:
        out[t] = _dedup_recent(out[t])

    n_with = sum(1 for t in tickers if out[t])
    print(f"[news_collector] {m.value} {n_with}/{len(tickers)}종목 뉴스 확보 · "
          + " · ".join(f"{k} {v}건" for k, v in stats.items())
          + (f" · 회사명 검색 {sum(1 for t in tickers if names.get(t.upper()) or names.get(t))}"
             f"/{len(tickers)}" if "google" in chain else ""))
    for src, why in skipped.items():
        print(f"[news_collector] ⚠️ {src} 차단기 작동: {why} (다른 소스로 진행)")
    if tickers and n_with == 0:
        print("[news_collector] ⚠️ 모든 소스에서 0건 — 네트워크/차단을 의심하세요. "
              f"소스={chain}")
    return out


# ──────────────────────────────────────────────────────────────────────────
# 시장 전체 헤드라인 — PM 의 시장 맥락
# ──────────────────────────────────────────────────────────────────────────
def market_headlines(fetch: Callable[[str], Optional[str]] = fetch_text,
                     sources: Optional[list[str]] = None,
                     limit: int = MARKET_HEADLINE_LIMIT,
                     days: int = 2,
                     market: Market | str = Market.US) -> list[NewsItem]:
    """공개 RSS 에서 시장 헤드라인. 최근 days 일 것만, 제목 중복 제거, 최신순."""
    m = _mkt(market)
    mod = news_module(m)
    chain = sources if sources is not None else market_sources_from_env(m)
    items: list[NewsItem] = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    for i, src in enumerate(chain):
        if i:
            time.sleep(REQUEST_GAP_SEC)
        url = mod.MARKET_FEEDS.get(src)
        if not url:
            continue
        body = fetch(url)
        if not body:
            continue
        for it in parse_rss(body, "", src):
            if re.match(r"\d{4}-\d{2}-\d{2}", it.published) and it.published < cutoff:
                continue
            items.append(it)
    return _dedup_recent(items, limit)


def market_headlines_text(fetch: Callable[[str], Optional[str]] = fetch_text,
                          sources: Optional[list[str]] = None,
                          limit: int = MARKET_HEADLINE_LIMIT,
                          market: Market | str = Market.US) -> str:
    items = market_headlines(fetch, sources, limit, market=market)
    return "\n".join(f"- {it.headline} ({it.source}, {it.published or '-'})" for it in items)


def market_news_text(fetch: Callable[[str], Optional[str]] = fetch_text,
                     market: Market | str = Market.US) -> tuple[str, str]:
    """(본문, 출처 라벨). 편집자 선정(세이브티커 PICK)이 있으면 그것, 없으면 공개 RSS.

    라벨은 PM 프롬프트 머리말과 로그에 쓴다 — 어느 쪽을 봤는지 나중에 알아야
    '뉴스가 성과를 더하는가' 표를 소스별로 나눌 수 있다.
    """
    m = _mkt(market)
    mod = news_module(m)
    try:
        picks = mod.market_picks_text()
    except Exception:  # noqa: BLE001
        picks = ""
    if picks:
        return picks, "SAVE PICK"
    text = market_headlines_text(fetch, market=m)
    if text:
        return text, "공개 RSS " + "/".join(market_sources_from_env(m))
    return "", ""
