"""
국내 관련 ETF 묶기 — 추천 종목마다 "같이 볼(또는 대신 살) ETF" 를 붙인다.

왜:
  개별 종목은 변동이 크고 실적·공시 리스크를 혼자 진다. 같은 섹터 ETF 는 그 테마에
  올라타되 종목 리스크를 나눈다. 사용자가 한국투자증권에서 바로 주문할 수 있는 것
  = **KRX 상장 ETF** 다. 그래서 묶음은 KRX 상장목록(FinanceDataReader 'ETF/KR')에서
  이름 키워드로 고른다 — 운용사별 코드 암기에 의존하지 않는다.

종류(kind):
  섹터    같은 섹터/테마 ETF (레버리지·인버스·해외 제외). 시총 큰 순 최대 3개
  그룹    '삼성그룹'·'현대차그룹' 처럼 그 회사 계열 ETF
  지수    KOSPI 종목 → KODEX 200, KOSDAQ 종목 → KODEX 코스닥150
  레버리지 같은 섹터 레버리지 1개(있을 때만) — 참고용, 권고 아님

데이터:
  FDR StockListing('ETF/KR') → universe/kr_etf_listing.json (7일 캐시)
  네트워크가 없으면 SEED_ETFS(대표 ETF 몇 개)로 돈다. 코드는 상장목록으로 검증하며,
  이름이 안 맞으면 버리고 경고한다 — 암기한 코드가 틀릴 수 있다는 전제.

섹터 정의(SECTORS)는 유니버스(업종→섹터)와 섹터 게이트(섹터 대표 ETF)도 같이 쓴다.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Iterable, Optional

REFRESH_SEC = 7 * 86400
MAX_SECTOR_ETFS = 3

# 섹터 키 → 대표 ETF(섹터 게이트용), 업종 키워드(KRX 업종명), 회사명 키워드, ETF 이름 키워드
# 대표 ETF 코드는 상장목록으로 검증된다(verify_seeds). 틀리면 로그에 남고 게이트에서 빠진다.
SECTORS: dict[str, dict] = {
    "반도체":    dict(etf="091160", etf_name="KODEX 반도체",
                   industry=["반도체"], names=["반도체", "하이닉스", "삼성전자", "한미반도체"],
                   etf_kw=["반도체"]),
    "2차전지":   dict(etf="305720", etf_name="KODEX 2차전지산업",
                   industry=["전지", "축전지"], names=["에너지솔루션", "배터리", "에코프로", "엘앤에프",
                                                "포스코퓨처엠", "삼성SDI", "SK이노베이션"],
                   etf_kw=["2차전지", "배터리"]),
    "바이오":    dict(etf="244580", etf_name="KODEX 바이오",
                   industry=["의약품", "의료용 물질", "생물학적"], names=["바이오", "제약", "팜", "셀트리온", "유한양행"],
                   etf_kw=["바이오", "제약"]),
    "헬스케어":  dict(etf="266420", etf_name="KODEX 헬스케어",
                   industry=["의료", "의료기기", "의료 정밀"], names=["메디", "헬스", "덴탈", "클래시스"],
                   etf_kw=["헬스케어"]),
    "은행":      dict(etf="091170", etf_name="KODEX 은행",
                   industry=["은행"], names=["금융지주", "은행", "KB금융", "신한지주", "하나금융", "우리금융", "카카오뱅크"],
                   etf_kw=["은행"]),
    "증권":      dict(etf="102970", etf_name="KODEX 증권",
                   industry=["증권", "금융 지원", "금융투자"], names=["증권", "투자증권", "미래에셋"],
                   etf_kw=["증권"]),
    "보험":      dict(etf="140700", etf_name="KODEX 보험",
                   industry=["보험"], names=["보험", "생명", "화재", "손해보험"],
                   etf_kw=["보험"]),
    "자동차":    dict(etf="091180", etf_name="KODEX 자동차",
                   industry=["자동차"], names=["현대차", "기아", "모비스", "자동차", "현대위아", "한온시스템"],
                   etf_kw=["자동차"]),
    "건설":      dict(etf="117700", etf_name="KODEX 건설",
                   industry=["건설", "건축", "토목"], names=["건설", "건축", "산업개발"],
                   etf_kw=["건설"]),
    "화학":      dict(etf="117460", etf_name="KODEX 에너지화학",
                   industry=["화학", "석유", "정제", "고무", "플라스틱"], names=["화학", "케미칼", "정유", "오일뱅크", "에너지", "S-Oil"],
                   etf_kw=["에너지화학", "화학"]),
    "철강":      dict(etf="117680", etf_name="KODEX 철강",
                   industry=["철강", "1차 금속", "1차 철강", "금속"], names=["철강", "제철", "스틸", "POSCO홀딩스", "고려아연"],
                   etf_kw=["철강"]),
    "조선":      dict(etf="", etf_name="",
                   industry=["선박", "조선", "보트"], names=["조선", "중공업", "오션", "해양", "삼성중공업", "HD현대"],
                   etf_kw=["조선"]),
    "기계로봇":  dict(etf="102960", etf_name="KODEX 기계장비",
                   industry=["기계", "장비", "일반 목적용 기계"], names=["기계", "로보틱스", "로봇", "두산"],
                   etf_kw=["기계", "로봇"]),
    "운송":      dict(etf="140710", etf_name="KODEX 운송",
                   industry=["운송", "항공", "해운", "물류", "육상"], names=["항공", "해운", "물류", "택배", "글로비스", "대한항공"],
                   etf_kw=["운송"]),
    "IT":        dict(etf="266370", etf_name="KODEX IT",
                   industry=["소프트웨어", "정보서비스", "통신", "전자부품", "컴퓨터", "영상", "통신장비"],
                   names=["NAVER", "카카오", "소프트", "테크", "시스템", "전자", "LG이노텍", "삼성SDS"],
                   etf_kw=["IT", "소프트웨어", "인터넷"]),
    "게임":      dict(etf="300950", etf_name="KODEX 게임산업",
                   industry=["게임"], names=["게임", "넷마블", "크래프톤", "엔씨소프트", "펄어비스", "카카오게임즈"],
                   etf_kw=["게임"]),
    "미디어엔터": dict(etf="266360", etf_name="KODEX 미디어&엔터테인먼트",
                   industry=["영상·오디오", "방송", "출판", "광고"], names=["엔터", "미디어", "CJ ENM", "하이브", "JYP", "에스엠", "스튜디오"],
                   etf_kw=["미디어", "엔터"]),
    "방산":      dict(etf="", etf_name="",
                   industry=["무기", "항공기", "우주"], names=["에어로스페이스", "방산", "LIG넥스원", "현대로템", "한화시스템", "풍산"],
                   etf_kw=["방산", "우주"]),
    "유통소비":  dict(etf="", etf_name="",
                   industry=["소매", "도매", "음식료", "식료품", "음료", "담배"], names=["백화점", "마트", "유통", "식품", "F&F", "아모레", "LG생활건강", "오리온", "CJ제일제당"],
                   etf_kw=["소비", "유통", "음식료", "필수소비"]),
    "원자력":    dict(etf="", etf_name="",
                   industry=["원자력", "전기업"], names=["두산에너빌리티", "한전", "원자력", "한국전력"],
                   etf_kw=["원자력"]),
}

# 그룹 ETF — 회사명이 이 접두로 시작하면 '<접두>그룹' ETF 를 붙인다
GROUP_PREFIX = ("삼성", "현대차", "LG", "SK", "한화", "포스코", "POSCO", "CJ", "두산", "롯데")

# 섹터 ETF 로 치지 않는 이름(해외·파생·채권·구조화)
_EXCLUDE = ("레버리지", "인버스", "선물", "채권", "커버드콜", "합성", "미국", "글로벌", "중국",
            "일본", "유로", "인도", "베트남", "달러", "금리", "TDF", "TRF", "혼합", "액티브채권")
_LEVERAGE = ("레버리지",)
_BRAND_RANK = {"KODEX": 0, "TIGER": 1, "KBSTAR": 2, "RISE": 2, "ACE": 3, "SOL": 4,
               "PLUS": 5, "HANARO": 6, "KoAct": 7, "TIMEFOLIO": 8, "KIWOOM": 9}

# 네트워크 없을 때의 최소 목록. 코드는 상장목록으로 검증한다.
SEED_ETFS: list[dict] = [
    {"code": "069500", "name": "KODEX 200"},
    {"code": "229200", "name": "KODEX 코스닥150"},
    {"code": "122630", "name": "KODEX 레버리지"},
    {"code": "091160", "name": "KODEX 반도체"},
    {"code": "091230", "name": "TIGER 반도체"},
    {"code": "091170", "name": "KODEX 은행"},
    {"code": "091180", "name": "KODEX 자동차"},
    {"code": "117700", "name": "KODEX 건설"},
    {"code": "117460", "name": "KODEX 에너지화학"},
    {"code": "117680", "name": "KODEX 철강"},
    {"code": "102970", "name": "KODEX 증권"},
    {"code": "140700", "name": "KODEX 보험"},
    {"code": "140710", "name": "KODEX 운송"},
    {"code": "102960", "name": "KODEX 기계장비"},
    {"code": "305720", "name": "KODEX 2차전지산업"},
    {"code": "305540", "name": "TIGER 2차전지테마"},
    {"code": "244580", "name": "KODEX 바이오"},
    {"code": "266420", "name": "KODEX 헬스케어"},
    {"code": "266370", "name": "KODEX IT"},
    {"code": "157490", "name": "TIGER 소프트웨어"},
    {"code": "300950", "name": "KODEX 게임산업"},
    {"code": "266360", "name": "KODEX 미디어&엔터테인먼트"},
    {"code": "102780", "name": "KODEX 삼성그룹"},
    {"code": "138540", "name": "TIGER 현대차그룹+펀더멘털"},
]


def listing_path() -> Path:
    from stock_auto.config.paths import universe_dir
    return universe_dir() / "kr_etf_listing.json"


def refresh(force: bool = False) -> dict:
    """KRX ETF 상장목록을 받아 캐시. 실패해도 예외를 올리지 않는다."""
    p = listing_path()
    if not force and p.exists() and time.time() - p.stat().st_mtime < REFRESH_SEC:
        return {"ok": True, "action": "cached", "n": len(_read(p))}
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing("ETF/KR")
        code_col = next((c for c in ("Symbol", "Code", "symbol") if c in df.columns), None)
        name_col = next((c for c in ("Name", "name") if c in df.columns), None)
        cap_col = next((c for c in ("MarCap", "Marcap", "MarketCap", "Amount") if c in df.columns), None)
        if code_col is None or name_col is None:
            return {"ok": False, "action": "skip", "detail": f"컬럼 없음 {list(df.columns)[:8]}"}
        rows = []
        for _, r in df.iterrows():
            code = str(r[code_col]).strip().zfill(6)
            name = str(r[name_col]).strip()
            if not code.isdigit() or not name or name == "nan":
                continue
            cap = None
            if cap_col is not None:
                try:
                    cap = float(r[cap_col])
                except (TypeError, ValueError):
                    cap = None
            rows.append({"code": code, "name": name, "marcap": cap})
        if len(rows) < 300:                 # 반쪽짜리 목록으로 캐시를 덮지 않는다
            return {"ok": False, "action": "skip", "detail": f"목록이 비정상({len(rows)}개)"}
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(p)
        return {"ok": True, "action": "refreshed", "n": len(rows)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": "fetch", "detail": f"{type(e).__name__}: {e}",
                "n": len(_read(p)) if p.exists() else 0}


def _read(p: Path) -> list[dict]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return [x for x in d if isinstance(x, dict) and x.get("code") and x.get("name")]
    except (OSError, json.JSONDecodeError):
        return []


def load_listing() -> list[dict]:
    """캐시된 상장목록. 없으면 SEED_ETFS."""
    p = listing_path()
    rows = _read(p) if p.exists() else []
    return rows or [dict(x, marcap=None) for x in SEED_ETFS]


def verify_seeds(listing: Optional[list[dict]] = None) -> list[str]:
    """SECTORS 의 대표 ETF 코드가 상장목록 이름과 맞는지. 틀린 것의 설명을 돌려준다."""
    listing = listing if listing is not None else load_listing()
    by_code = {x["code"]: x["name"] for x in listing}
    if not by_code or set(by_code) == {x["code"] for x in SEED_ETFS}:
        return []                        # 시드 자체와 비교하는 건 의미가 없다
    bad = []
    for key, s in SECTORS.items():
        if not s["etf"]:
            continue
        actual = by_code.get(s["etf"])
        if actual is None:
            bad.append(f"{key}: {s['etf']} 상장목록에 없음")
        elif not any(k in actual for k in s["etf_kw"]):
            bad.append(f"{key}: {s['etf']} 는 '{actual}' — 기대 '{s['etf_name']}'")
    return bad


def sector_key_for(name: str = "", industry: str = "") -> str:
    """회사명·KRX 업종명 → 섹터 키. 못 찾으면 ''.
    업종명이 더 믿을 만하므로 먼저 보고, 회사명 키워드로 보강한다."""
    ind = (industry or "").strip()
    nm = (name or "").strip()
    if ind:
        for key, s in SECTORS.items():
            if any(k in ind for k in s["industry"]):
                return key
    if nm:
        for key, s in SECTORS.items():
            if any(k in nm for k in s["names"]):
                return key
    return ""


def sector_etf_for_key(key: str) -> str:
    """섹터 게이트·유니버스용 대표 ETF 코드. 없으면 '-'."""
    s = SECTORS.get(key)
    return (s or {}).get("etf") or "-"


def gate_tickers() -> dict[str, dict]:
    """섹터 게이트(sector_status)가 진단할 KRX 섹터 ETF — {코드: {name, gics}}."""
    return {s["etf"]: {"name": s["etf_name"], "gics": key}
            for key, s in SECTORS.items() if s["etf"]}


def _brand(name: str) -> int:
    head = name.split()[0] if name else ""
    return _BRAND_RANK.get(head, 50)


def _rank_key(x: dict) -> tuple:
    cap = x.get("marcap")
    return (0 if cap else 1, -(cap or 0), _brand(x["name"]))


def related_etfs(code: str, name: str = "", sector_key: str = "",
                 segment: str = "", listing: Optional[list[dict]] = None,
                 max_sector: int = MAX_SECTOR_ETFS) -> list[dict]:
    """종목 1개의 관련 ETF — [{code, name, kind}]. 섹터 → 그룹 → 지수 → 레버리지 순."""
    listing = listing if listing is not None else load_listing()
    key = sector_key or sector_key_for(name, "")
    out: list[dict] = []
    seen: set[str] = set()

    def _add(x: dict, kind: str) -> None:
        if x["code"] in seen:
            return
        seen.add(x["code"])
        out.append({"code": x["code"], "name": x["name"], "kind": kind})

    if key and key in SECTORS:
        kws = SECTORS[key]["etf_kw"]
        hits = [x for x in listing
                if any(k in x["name"] for k in kws)
                and not any(e in x["name"] for e in _EXCLUDE)]
        # 'IT' 같은 두 글자 키워드는 'KODEX IT' 처럼 단어로만 — 'KBSTAR ITX' 오매칭 방지
        if key == "IT":
            hits = [x for x in hits if re.search(r"(^|\s)IT($|\s)", x["name"])
                    or "소프트웨어" in x["name"] or "인터넷" in x["name"]]
        for x in sorted(hits, key=_rank_key)[:max_sector]:
            _add(x, "섹터")
        lev = [x for x in listing
               if any(k in x["name"] for k in kws) and any(l in x["name"] for l in _LEVERAGE)
               and not any(e in x["name"] for e in ("미국", "글로벌", "중국", "일본"))]
        for x in sorted(lev, key=_rank_key)[:1]:
            _add(x, "레버리지")

    nm = (name or "").strip()
    for pre in GROUP_PREFIX:
        if nm.startswith(pre) or nm.replace(" ", "").startswith(pre):
            grp = [x for x in listing if f"{pre}그룹" in x["name"]
                   and not any(e in x["name"] for e in _EXCLUDE)]
            for x in sorted(grp, key=_rank_key)[:1]:
                _add(x, "그룹")
            break

    idx_name = "코스닥150" if (segment or "").upper().startswith("KOSDAQ") else "200"
    idx = [x for x in listing if x["name"] in (f"KODEX {idx_name}", f"TIGER {idx_name}")]
    for x in sorted(idx, key=_rank_key)[:1]:
        _add(x, "지수")
    return out


def format_line(etfs: Iterable[dict]) -> str:
    """'KODEX 반도체(091160) · TIGER 반도체(091230) · 지수 KODEX 200(069500) · 레버리지 …'"""
    parts = []
    for e in etfs:
        tag = "" if e.get("kind") == "섹터" else f"{e.get('kind')} "
        parts.append(f"{tag}{e['name']}({e['code']})")
    return " · ".join(parts)


def bundle(codes: Iterable[str], names: Optional[dict[str, str]] = None,
           sectors: Optional[dict[str, str]] = None,
           segments: Optional[dict[str, str]] = None) -> dict[str, list[dict]]:
    """여러 종목 → {code: [etf…]}. 유니버스 캐시(이름·섹터·시장구분)를 쓴다."""
    names = names or {}
    sectors = sectors or {}
    segments = segments or {}
    if not names or not sectors:
        try:
            from stock_auto.markets.kr import universe as kru
            names = names or kru.load_names(codes)
            sectors = sectors or kru.load_sectors(codes)
            segments = segments or kru.load_segments(codes)
        except Exception:  # noqa: BLE001
            pass
    listing = load_listing()
    return {c: related_etfs(c, names.get(c, ""), sectors.get(c, ""), segments.get(c, ""), listing)
            for c in codes}


if __name__ == "__main__":
    import sys
    r = refresh(force="--force" in sys.argv)
    print(r)
    bad = verify_seeds()
    print("대표 ETF 검증:", "전부 일치" if not bad else bad)
    codes = [a for a in sys.argv[1:] if not a.startswith("--")]
    for c, es in bundle(codes).items():
        print(f"  {c}: {format_line(es) or '관련 ETF 없음'}")
