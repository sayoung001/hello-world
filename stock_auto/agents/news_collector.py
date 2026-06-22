"""
뉴스 수집 — saveticker.com/news 크롤링 (미국 종목, 사용자 지정 소스).

주의(사용자 고지):
- saveticker는 '미국 티커 중심' 뉴스 집계. KR 종목 뉴스는 거의 미커버 → KR 뉴스원 별도 필요.
- 크롤링은 사이트 robots/ToS 준수 전제. rate limit 위해 캐시·간격 둘 것.

의존성 최소화: requests + 경량 정규식 파서(bs4 있으면 사용). 네트워크 불가 환경에서는
parse_html()에 미리 받은 HTML을 넣어 파서 로직만 검증할 수 있다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Optional

SAVETICKER_NEWS_URL = "https://www.saveticker.com/news"


@dataclass
class NewsItem:
    ticker: str
    headline: str
    url: str = ""
    source: str = "saveticker"
    published: str = ""


def fetch_html(url: str = SAVETICKER_NEWS_URL, timeout: int = 10) -> Optional[str]:
    """뉴스 페이지 HTML 가져오기. 실패 시 None(조용한 실패 금지 → 사유 출력)."""
    try:
        import requests
        headers = {"User-Agent": "Mozilla/5.0 (StockAuto news collector)"}
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001
        print(f"[news_collector] fetch 실패: {type(e).__name__}: {e}")
        return None


def parse_html(html_text: str) -> list[NewsItem]:
    """
    HTML → NewsItem 리스트.
    1차: bs4(있으면). 2차: 정규식 폴백.
    saveticker의 정확한 DOM은 운영 중 확정 → 여기서는 일반적 앵커/티커 패턴을 추출하고,
    실제 셀렉터는 라이브 검증 후 보정한다(아래 _SELECTORS).
    """
    items: list[NewsItem] = []
    try:
        from bs4 import BeautifulSoup  # type: ignore
        soup = BeautifulSoup(html_text, "html.parser")
        # 헤드라인 후보: 기사 링크 앵커
        for a in soup.find_all("a"):
            text = (a.get_text() or "").strip()
            href = a.get("href", "")
            if len(text) < 25:        # 너무 짧으면 헤드라인 아님
                continue
            ticker = _guess_ticker(text) or _guess_ticker(href)
            items.append(NewsItem(ticker=ticker or "", headline=html.unescape(text),
                                  url=href))
    except ImportError:
        # 정규식 폴백: <a href="...">텍스트</a>
        for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                             html_text, re.DOTALL | re.IGNORECASE):
            href, raw = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
            text = html.unescape(raw)
            if len(text) < 25:
                continue
            ticker = _guess_ticker(text) or _guess_ticker(href)
            items.append(NewsItem(ticker=ticker or "", headline=text, url=href))
    return items


_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")


def _guess_ticker(text: str) -> Optional[str]:
    """대문자 토막에서 티커 추정($AAPL, (AAPL), AAPL 등). 보수적."""
    if not text:
        return None
    m = re.search(r"\$([A-Z]{1,5})\b", text) or re.search(r"\(([A-Z]{1,5})\)", text)
    if m:
        return m.group(1)
    return None


def collect_for_tickers(tickers: list[str],
                        html_text: Optional[str] = None) -> dict[str, list[NewsItem]]:
    """
    대상 티커별 뉴스 매핑. html_text 미지정 시 라이브 fetch.
    추천 상위 종목만 넘겨 토큰/요청을 아낀다(사용자 정책).
    """
    if html_text is None:
        html_text = fetch_html()
    if not html_text:
        return {t: [] for t in tickers}

    all_items = parse_html(html_text)
    wanted = {t.upper() for t in tickers}
    out: dict[str, list[NewsItem]] = {t: [] for t in tickers}
    for it in all_items:
        tk = it.ticker.upper()
        if tk in wanted:
            out[tk].append(it)
        else:
            # 티커 추정 실패분: 헤드라인에 종목명/티커가 들어간 경우 매칭
            for t in tickers:
                if re.search(rf"\b{re.escape(t)}\b", it.headline, re.IGNORECASE):
                    out[t].append(it)
                    break
    return out
