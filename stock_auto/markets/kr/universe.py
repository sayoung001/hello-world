"""
국내 유니버스 — KRX 상장목록(시가총액 상위 N) + 업종 → 섹터 키 → 섹터 대표 ETF.

출처(FinanceDataReader):
  StockListing('KRX')       Code, Name, Market(KOSPI/KOSDAQ), Marcap … — 시총 정렬용
  StockListing('KRX-DESC')  Code, Name, Sector(업종), Industry(주요제품) — 섹터 매핑용
                            (없거나 실패하면 회사명 키워드만으로 매핑한다)

캐시(universe/):
  kr_names.json     {코드: 회사명}       뉴스 검색어·ETF 그룹 매칭
  kr_sectors.json   {코드: 섹터 키}      관련 ETF 묶기
  kr_segments.json  {코드: KOSPI|KOSDAQ} 지수 ETF 선택
  universe_KR.csv   {코드: 섹터 ETF}     (config.universe 의 스냅샷 규약과 동일)

왜 상위 N(기본 300)인가: 실시간 모니터·LLM 비용이 아니라 **유동성** 때문이다. 시총
하위 종목은 거래대금 사전필터에서 어차피 빠지므로 받아 둘 이유가 없다.
STOCK_KR_UNIVERSE_N 으로 바꿀 수 있다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable, Optional

from stock_auto.config.settings import Market
from stock_auto.markets.kr import etf as kr_etf

DEFAULT_TOP_N = int(os.environ.get("STOCK_KR_UNIVERSE_N", "300") or 300)


def _p(name: str) -> Path:
    from stock_auto.config.paths import universe_dir
    return universe_dir() / name


def names_path() -> Path:
    return _p("kr_names.json")


def sectors_path() -> Path:
    return _p("kr_sectors.json")


def segments_path() -> Path:
    return _p("kr_segments.json")


def _pick(df, names: tuple[str, ...]) -> Optional[str]:
    return next((c for c in names if c in df.columns), None)


def _load_desc() -> dict[str, tuple[str, str]]:
    """{코드: (Sector, Industry)} — KRX-DESC. 실패하면 빈 dict(회사명 매핑으로 간다)."""
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX-DESC")
        code_col = _pick(df, ("Code", "Symbol", "code"))
        sec_col = _pick(df, ("Sector", "sector"))
        ind_col = _pick(df, ("Industry", "industry"))
        if code_col is None or (sec_col is None and ind_col is None):
            return {}
        out = {}
        for _, r in df.iterrows():
            code = str(r[code_col]).strip().zfill(6)
            sec = str(r[sec_col]).strip() if sec_col else ""
            ind = str(r[ind_col]).strip() if ind_col else ""
            out[code] = ("" if sec == "nan" else sec, "" if ind == "nan" else ind)
        return out
    except Exception as e:  # noqa: BLE001
        print(f"[kr-universe] 업종 목록(KRX-DESC) 실패({type(e).__name__}) — 회사명 키워드로 매핑")
        return {}


def build(top_n: Optional[int] = None, save: bool = True) -> dict[str, str]:
    """{코드: 섹터 ETF}. 이름·섹터·시장구분 캐시를 함께 쓴다. 실패 시 빈 dict(호출측 폴백)."""
    top_n = top_n or DEFAULT_TOP_N
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("KRX")
    except Exception as e:  # noqa: BLE001
        print(f"[kr-universe] KRX 상장목록 실패({type(e).__name__}: {str(e)[:80]})")
        return {}
    code_col = _pick(df, ("Code", "Symbol", "code"))
    name_col = _pick(df, ("Name", "name"))
    cap_col = _pick(df, ("Marcap", "MarCap", "MarketCap", "marcap"))
    seg_col = _pick(df, ("Market", "market"))
    if code_col is None:
        print(f"[kr-universe] 심볼 컬럼 없음 (컬럼 {list(df.columns)[:8]})")
        return {}
    if cap_col is not None:
        df = df.sort_values(cap_col, ascending=False)
    else:
        print("[kr-universe] ⚠️ 시총 컬럼 없음 — 정렬 없이 앞에서 자릅니다")
    desc = _load_desc()

    uni: dict[str, str] = {}
    names: dict[str, str] = {}
    sectors: dict[str, str] = {}
    segments: dict[str, str] = {}
    for _, r in df.iterrows():
        code = str(r[code_col]).strip().zfill(6)
        if not code.isdigit():
            continue
        seg = str(r[seg_col]).strip().upper() if seg_col else ""
        if seg and seg not in ("KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"):
            continue                          # KONEX 등 제외
        name = str(r[name_col]).strip() if name_col else ""
        if _is_non_common(name):
            continue                          # 우선주·스팩·리츠는 유니버스 밖
        sec, ind = desc.get(code, ("", ""))
        key = kr_etf.sector_key_for(name, sec or ind)
        uni[code] = kr_etf.sector_etf_for_key(key) if key else "-"
        names[code] = name
        sectors[code] = key
        segments[code] = "KOSDAQ" if seg.startswith("KOSDAQ") else "KOSPI"
        if len(uni) >= top_n:
            break

    n_sec = sum(1 for v in sectors.values() if v)
    print(f"[kr-universe] KRX 시총 상위 {len(uni)}종목 · 섹터 매핑 {n_sec}/{len(uni)}"
          + ("" if desc else " (업종 목록 없음 — 회사명 키워드만)"))
    if save and uni:
        _save_json(names_path(), names)
        _save_json(sectors_path(), sectors)
        _save_json(segments_path(), segments)
        try:
            from stock_auto.config.universe import save_snapshot
            save_snapshot(uni, Market.KR)
        except Exception as e:  # noqa: BLE001
            print(f"[kr-universe] 스냅샷 저장 실패: {type(e).__name__}")
    return uni


_NON_COMMON = ("스팩", "SPAC", "리츠", "REIT", "우B", "우C")


def _is_non_common(name: str) -> bool:
    """우선주(…우, 1우, 2우B)·스팩·리츠 제외."""
    if not name:
        return False
    if any(k in name for k in _NON_COMMON):
        return True
    import re
    return bool(re.search(r"\d?우$", name))


def _save_json(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


def _load_json(p: Path, codes: Optional[Iterable[str]]) -> dict[str, str]:
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    d = {str(k): str(v) for k, v in d.items() if v}
    if codes is not None:
        want = set(codes)
        d = {k: v for k, v in d.items() if k in want}
    return d


def load_names(codes: Optional[Iterable[str]] = None) -> dict[str, str]:
    return _load_json(names_path(), codes)


def load_sectors(codes: Optional[Iterable[str]] = None) -> dict[str, str]:
    return _load_json(sectors_path(), codes)


def load_segments(codes: Optional[Iterable[str]] = None) -> dict[str, str]:
    return _load_json(segments_path(), codes)


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    uni = build(n)
    names = load_names()
    for code in list(uni)[:15]:
        print(f"  {code} {names.get(code, '?'):14} 섹터ETF {uni[code]}")
