"""
뉴스 수집 — 티커별 RSS(야후·구글뉴스) 우선, saveticker 는 보조.

왜 바꿨나:
  원래는 saveticker.com/news 한 페이지만 긁었다. VM 에서 매 배치 403 이
  났고(봇 식별 UA + 사이트 차단), 소스가 하나뿐이라 그 순간 15종목 전부가
  "수집된 뉴스 없음"이 됐다. 분석 3종 중 뉴스 다리가 운영에서 늘 죽어 있던
  셈이다. 게다가 saveticker 파서는 DOM 을 라이브로 확정한 적이 없었다.

  RSS 는 기계용 공개 피드라 차단이 드물고 형식이 안정적이다. 종목별로
  받으므로 "전체 뉴스 페이지에서 티커를 추측해 매칭"하던 불확실한 단계도
  사라진다. 소스를 체인으로 두어 하나가 죽어도 나머지로 간다.

소스(STOCK_NEWS_SOURCES 로 순서·선택, 기본 "yahoo,google"):
  yahoo       https://feeds.finance.yahoo.com/rss/2.0/headline?s=<T>
  google      https://news.google.com/rss/search?q=<T>+stock
  saveticker  https://www.saveticker.com/news  (브라우저 UA, 티커 추측 매칭)

의존성: requests + 표준 xml.etree. feedparser 등 추가 설치 없음.
네트워크 없는 환경에서는 parse_rss()/parse_html() 에 받아 둔 본문을 넣어
파서만 검증할 수 있다.
"""

from __future__ import annotations

import html
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Optional

SAVETICKER_NEWS_URL = "https://www.saveticker.com/news"
YAHOO_RSS = ("https://feeds.finance.yahoo.com/rss/2.0/headline"
             "?s={ticker}&region=US&lang=en-US")
GOOGLE_RSS = ("https://news.google.com/rss/search"
              "?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en")

DEFAULT_SOURCES = "saveticker,yahoo,google"
MAX_PER_TICKER = 15          # NewsAgent 가 news[:15] 만 쓴다
REQUEST_GAP_SEC = 0.3        # 종목별 연속 요청 사이 간격(예의상 rate limit)

# 봇 식별 UA("StockAuto news collector")가 403 의 한 원인이었다.
# 일반 브라우저 UA 로 보낸다. RSS 는 어차피 공개 피드다.
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
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
def fetch_text(url: str, timeout: int = 10) -> Optional[str]:
    """GET → 본문. 실패는 사유를 찍고 None (조용한 실패 금지)."""
    try:
        import requests
        r = requests.get(url, headers=_HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001
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
        if len(title) < 15:
            continue
        link = (item.findtext("link") or "").strip()
        src_el = item.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else default_source
        # 구글뉴스는 제목 끝에 " - 매체명" 을 붙인다. 같은 기사가 야후에서는
        # 꼬리 없이 와서 중복 제거가 안 되고 LLM 이 같은 헤드라인을 두 번 본다.
        # 매체명은 source 에 따로 있으니 제목에서는 떼어낸다.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(f" - {source}")].rstrip()
        pub = item.findtext("pubDate") or ""
        out.append(NewsItem(ticker=ticker.upper(), headline=title, url=link,
                            source=source or default_source, published=_iso(pub)))
    return out


# ──────────────────────────────────────────────────────────────────────────
# saveticker HTML 파서 — 보조 소스로만 남긴다
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


def _best_title(it) -> str:
    """한글 번역 제목이 있으면 그걸, 없으면 영문. LLM 은 한국어로 쓴다."""
    t = (it.title or "").replace("[SAVE PICK]", "").strip()
    return t or (it.title_en or "").strip()


def market_picks_text(days: int = 1, limit: int = 20) -> str:
    """오늘의 SAVE PICK(편집자 선정 주요뉴스)을 종목 무관하게 한 덩어리로.

    종목별 NewsAgent 가 아니라 **PortfolioAgent 의 시장 맥락**으로 들어간다.
    모든 종목 호출에 같은 덩어리가 들어가므로 1,024 토큰을 넘기면 프롬프트
    캐시를 탄다. 데이터가 없으면 빈 문자열 — 호출측이 섹션을 생략한다.
    """
    try:
        from stock_auto.data import saveticker_store as SS
        items = SS.picks(days=days)[:limit]
    except Exception:  # noqa: BLE001
        return ""
    if not items:
        return ""
    lines = []
    for it in items:
        tk = f" [{' '.join(it.tickers[:4])}]" if it.tickers else ""
        lines.append(f"- {_best_title(it)}{tk} ({it.source or 'saveticker'}, {it.kst_date})")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# 시장 전체 헤드라인 — 세이브티커 SAVE PICK 이 없을 때의 공개 RSS 대체
# ──────────────────────────────────────────────────────────────────────────
# 종목별 뉴스는 위 체인(야후·구글)이 이미 받는다. 비어 있던 것은 **시장 맥락**
# 블록이었다: PM 프롬프트의 "오늘 주요뉴스"가 세이브티커 PICK 만 보고 있어서
# PC 수집이 없는 날은 PM 이 시장 분위기를 전혀 모른 채 종목을 판단했다.
# 공개 RSS 는 편집자 선별(PICK)만큼 정제돼 있지는 않지만, 없는 것보다 낫다.
MARKET_FEEDS: dict[str, str] = {
    "cnbc": "https://www.cnbc.com/id/100003114/device/rss/rss.html",       # Top News
    "cnbc_finance": "https://www.cnbc.com/id/10000664/device/rss/rss.html",  # Finance
    "marketwatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "yahoo_market": "https://finance.yahoo.com/news/rssindex",
    "google_market": ("https://news.google.com/rss/search"
                      "?q=stock+market+when:1d&hl=en-US&gl=US&ceid=US:en"),
}
DEFAULT_MARKET_SOURCES = "cnbc,marketwatch,yahoo_market"
MARKET_HEADLINE_LIMIT = 20


def market_sources_from_env() -> list[str]:
    raw = os.environ.get("STOCK_MARKET_NEWS_SOURCES", DEFAULT_MARKET_SOURCES)
    return [s.strip().lower() for s in raw.split(",") if s.strip() and s.strip().lower() in MARKET_FEEDS]


def market_headlines(fetch: Callable[[str], Optional[str]] = fetch_text,
                     sources: Optional[list[str]] = None,
                     limit: int = MARKET_HEADLINE_LIMIT,
                     days: int = 2) -> list[NewsItem]:
    """공개 RSS 에서 시장 헤드라인. 최근 days 일 것만, 제목 중복 제거, 최신순."""
    chain = sources if sources is not None else market_sources_from_env()
    items: list[NewsItem] = []
    cutoff = ""
    try:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        pass
    for i, src in enumerate(chain):
        if i:
            time.sleep(REQUEST_GAP_SEC)
        body = fetch(MARKET_FEEDS[src])
        if not body:
            continue
        for it in parse_rss(body, "", src):
            # 날짜를 읽을 수 있고 너무 오래됐으면 버린다. 날짜 없는 건 둔다.
            if cutoff and re.match(r"\d{4}-\d{2}-\d{2}", it.published) and it.published < cutoff:
                continue
            items.append(it)
    return _dedup_recent(items, limit)


def market_headlines_text(fetch: Callable[[str], Optional[str]] = fetch_text,
                          sources: Optional[list[str]] = None,
                          limit: int = MARKET_HEADLINE_LIMIT) -> str:
    items = market_headlines(fetch, sources, limit)
    return "\n".join(f"- {it.headline} ({it.source}, {it.published or '-'})" for it in items)


def market_news_text(fetch: Callable[[str], Optional[str]] = fetch_text
                     ) -> tuple[str, str]:
    """(본문, 출처 라벨). 세이브티커 PICK 이 있으면 그것, 없으면 공개 RSS.

    라벨은 PM 프롬프트 머리말과 로그에 쓴다 — 어느 쪽을 봤는지 나중에 알아야
    '뉴스가 성과를 더하는가' 표를 소스별로 나눌 수 있다.
    """
    picks = market_picks_text()
    if picks:
        return picks, "SAVE PICK"
    text = market_headlines_text(fetch)
    if text:
        srcs = market_sources_from_env()
        return text, "공개 RSS " + "/".join(srcs)
    return "", ""


def sources_from_env() -> list[str]:
    raw = os.environ.get("STOCK_NEWS_SOURCES", DEFAULT_SOURCES)
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def collect_for_tickers(tickers: list[str],
                        html_text: Optional[str] = None,
                        fetch: Callable[[str], Optional[str]] = fetch_text,
                        sources: Optional[list[str]] = None,
                        ) -> dict[str, list[NewsItem]]:
    """
    티커별 뉴스. 소스 체인을 순서대로 시도하고 결과를 합친다.

    html_text 를 주면 (호환·테스트용) saveticker 파서만 돌린다.
    fetch 를 바꿔 끼우면 네트워크 없이 체인 로직을 검증할 수 있다.
    """
    tickers = [t for t in tickers if t]
    if html_text is not None:
        return _match_saveticker(parse_html(html_text), tickers)

    chain = sources if sources is not None else sources_from_env()
    out: dict[str, list[NewsItem]] = {t: [] for t in tickers}
    stats: dict[str, int] = {}

    # 종목별 RSS 소스
    for src in chain:
        if src not in ("yahoo", "google"):
            continue
        tmpl = YAHOO_RSS if src == "yahoo" else GOOGLE_RSS
        got = 0
        for i, t in enumerate(tickers):
            if i:
                time.sleep(REQUEST_GAP_SEC)
            body = fetch(tmpl.format(ticker=t))
            if not body:
                continue
            items = parse_rss(body, t, src)
            out[t].extend(items)
            got += len(items)
        stats[src] = got

    # saveticker — VM 이 직접 받지 않는다(클라우드플레어). PC 확장이 수집해 git
    # 저장소에 올린 것을 로컬에서 읽는다. 종목 태그가 있어 추측 매칭이 없다.
    if "saveticker" in chain:
        try:
            from stock_auto.data import saveticker_store as SS
            got = 0
            for t, its in SS.items_for(tickers, days=2).items():
                for it in its[:MAX_PER_TICKER]:
                    out[t].append(NewsItem(
                        ticker=t, headline=_best_title(it), url=it.url,
                        source=("SAVE PICK" if it.is_pick else (it.source or "saveticker")),
                        published=it.kst_date))
                    got += 1
            stats["saveticker"] = got
        except Exception as e:  # noqa: BLE001 — 데이터 저장소가 없어도 체인은 간다
            print(f"[news_collector] saveticker 로컬 읽기 실패: {type(e).__name__}: {e}")
            stats["saveticker"] = 0

    for t in tickers:
        out[t] = _dedup_recent(out[t])

    n_with = sum(1 for t in tickers if out[t])
    print(f"[news_collector] {n_with}/{len(tickers)}종목 뉴스 확보 · "
          + " · ".join(f"{k} {v}건" for k, v in stats.items()))
    if tickers and n_with == 0:
        print("[news_collector] ⚠️ 모든 소스에서 0건 — 네트워크/차단을 의심하세요. "
              f"소스={chain}")
    return out
