"""
미국 종목 → KIS 거래소 코드(NAS / NYS / AMS).

왜 필요한가:
  KIS 해외 실시간 체결(HDFSCNT0)은 구독 키가 'D' + 거래소코드 + 종목코드다.
  DNASAAPL 은 되지만 DNASBLK 는 아무것도 오지 않는다 — BLK 는 NYSE 라서
  DNYSBLK 여야 한다. 피드는 거래소를 하나만 받았고(기본 NAS) 모든 종목에
  그 코드를 붙였다. 유니버스(S&P 500)의 **3분의 2가 NYSE** 이므로 그 종목들은
  구독은 되는데 체결이 영영 안 들어온다. 오류도 없다.

출처:
  나스닥 트레이더 심볼 디렉터리(공개, 매일 갱신):
    nasdaqlisted.txt — 나스닥 상장 → NAS
    otherlisted.txt  — 그 외. Exchange 열: N=NYSE → NYS, A=NYSE American → AMS,
                       P=NYSE Arca → AMS(KIS 는 Arca ETF 를 아멕스로 분류),
                       Z=Cboe BZX → AMS, V=IEX → AMS
  캐시: $STOCK_DATA_DIR/universe/us_exchange.json (7일마다 갱신, 실패 시 옛 캐시)
  수동 보정: universe/us_exchange_override.json {"SYMB": "NYS"} 가 있으면 우선.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
OTHER_CODE = {"N": "NYS", "A": "AMS", "P": "AMS", "Z": "AMS", "V": "AMS"}
VALID = ("NAS", "NYS", "AMS")
REFRESH_SEC = 7 * 86400
DEFAULT = os.environ.get("KIS_US_EXCHANGE_DEFAULT", "NAS").strip().upper() or "NAS"


def cache_path() -> Path:
    from stock_auto.config.paths import universe_dir
    return universe_dir() / "us_exchange.json"


def override_path() -> Path:
    from stock_auto.config.paths import universe_dir
    return universe_dir() / "us_exchange_override.json"


def names_path() -> Path:
    """{symbol: 회사명} — 뉴스 검색어용. 'IT stock' 은 쓰레기를 받지만 'Gartner' 는 아니다."""
    from stock_auto.config.paths import universe_dir
    return universe_dir() / "us_names.json"


def load_names(symbols: Optional[Iterable[str]] = None) -> dict[str, str]:
    p = names_path()
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    d = {str(k).upper(): str(v) for k, v in d.items() if v}
    if symbols is not None:
        want = {s.upper() for s in symbols}
        d = {k: v for k, v in d.items() if k in want}
    return d


def _fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_listing_full(nasdaq_text: str, other_text: str
                       ) -> tuple[dict[str, str], dict[str, str]]:
    """두 파일 → ({symbol: NAS|NYS|AMS}, {symbol: 회사명}).
    테스트 이슈·마지막 'File Creation Time' 줄 제외."""
    exch: dict[str, str] = {}
    names: dict[str, str] = {}
    for line in nasdaq_text.splitlines()[1:]:
        c = line.split("|")
        if len(c) < 4 or c[0].startswith("File Creation") or not c[0]:
            continue
        if c[3].strip() == "Y":            # Test Issue
            continue
        s = c[0].strip()
        exch[s] = "NAS"
        names[s] = clean_name(c[1])
    for line in other_text.splitlines()[1:]:
        c = line.split("|")
        if len(c) < 7 or c[0].startswith("File Creation") or not c[0]:
            continue
        if c[6].strip() == "Y":            # Test Issue
            continue
        code = OTHER_CODE.get(c[2].strip())
        if code:
            s = c[0].strip()
            exch.setdefault(s, code)
            names.setdefault(s, clean_name(c[1]))
    return exch, names


def parse_listing(nasdaq_text: str, other_text: str) -> dict[str, str]:
    return parse_listing_full(nasdaq_text, other_text)[0]


_NAME_CUT = re.compile(
    r"\s+(?:-\s+)?(?:Common Stock|Class [A-Z]\b|Ordinary Shares|American Depositary"
    r"|Depositary|Common Shares|Units?\b|Warrants?\b|Preferred).*$", re.IGNORECASE)
_NAME_SUFFIX = re.compile(
    r"(?:,?\s+(?:Inc\.?|Incorporated|Corporation|Corp\.?|Company|Co\.?|plc|PLC|Ltd\.?"
    r"|Limited|N\.V\.|S\.A\.|AG|SE|LP|L\.P\.))+\s*$")


def clean_name(raw: str) -> str:
    """'Gartner, Inc. Common Stock' → 'Gartner'. 뉴스 검색어로 쓸 수 있는 꼴로.
    법인 접미사와 증권 종류를 떼고, 'Hilton Worldwide Holdings' 처럼 뜻이 있는 말은 남긴다."""
    s = (raw or "").strip()
    s = _NAME_CUT.sub("", s)
    s = _NAME_SUFFIX.sub("", s)
    s = s.rstrip(" ,.-")
    return s if len(s) >= 3 else (raw or "").strip()


def refresh(force: bool = False) -> dict:
    """캐시를 갱신한다. 실패해도 예외를 올리지 않는다(실시간 감시가 멈추면 안 된다)."""
    p = cache_path()
    if not force and p.exists() and time.time() - p.stat().st_mtime < REFRESH_SEC:
        return {"ok": True, "action": "cached", "n": len(_read(p))}
    try:
        m, names = parse_listing_full(_fetch(NASDAQ_LISTED), _fetch(OTHER_LISTED))
        if len(m) < 3000:                  # 반쪽짜리 파일로 캐시를 덮지 않는다
            return {"ok": False, "action": "skip", "detail": f"목록이 비정상({len(m)}종목)"}
        p.parent.mkdir(parents=True, exist_ok=True)
        for path, data in ((p, m), (names_path(), names)):
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(path)
        return {"ok": True, "action": "refreshed", "n": len(m)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": "fetch", "detail": f"{type(e).__name__}: {e}",
                "n": len(_read(p)) if p.exists() else 0}


def _read(p: Path) -> dict[str, str]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {str(k).upper(): str(v).upper() for k, v in d.items()
                if str(v).upper() in VALID}
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}


def load_map(symbols: Optional[Iterable[str]] = None) -> dict[str, str]:
    """{symbol: 거래소코드}. 캐시 + 수동 보정. 필요하면 먼저 refresh()."""
    m = _read(cache_path()) if cache_path().exists() else {}
    ov = _read(override_path()) if override_path().exists() else {}
    m.update(ov)
    if symbols is not None:
        want = {s.upper() for s in symbols}
        m = {k: v for k, v in m.items() if k in want}
    return m


def exchange_for(symbol: str, mapping: Optional[dict[str, str]] = None,
                 default: str = DEFAULT) -> str:
    m = mapping if mapping is not None else load_map()
    return m.get(symbol.upper(), default)


def summary(symbols: Iterable[str], mapping: Optional[dict[str, str]] = None) -> str:
    syms = list(symbols)
    m = mapping if mapping is not None else load_map(syms)
    counts: dict[str, int] = {}
    unknown = []
    for s in syms:
        code = m.get(s.upper())
        if code:
            counts[code] = counts.get(code, 0) + 1
        else:
            unknown.append(s)
    body = " · ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "매핑 없음"
    if unknown:
        body += (f" · 미상 {len(unknown)}(→{DEFAULT}): "
                 + " ".join(unknown[:8]) + (" …" if len(unknown) > 8 else ""))
    return f"거래소 매핑 {body}"


if __name__ == "__main__":
    import sys
    r = refresh(force="--force" in sys.argv)
    print(r)
    syms = [a for a in sys.argv[1:] if not a.startswith("--")]
    if syms:
        m = load_map(syms)
        for s in syms:
            print(f"  {s:6} → {m.get(s.upper(), DEFAULT + '(기본)')}")
