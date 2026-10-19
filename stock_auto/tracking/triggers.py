"""
진입 트리거 — "추천의 진입 조건을 기록하고, 그 가격에 도달하면 알린다".

흐름:
  일일 배치가 추천을 내면        → register()   트리거를 pending 으로 적립
  장중 모니터 / 일봉 사후점검    → evaluate()   도달 여부 판정
  도달하면                       → fire()       Notion 정리 + Telegram 단축 알림(링크)
  horizon 안에 못 닿으면          → expired

왜 별도 저장소인가:
  signals.csv 는 '추천 1건 = 1행'이고 라벨러가 결과를 채우는 구조다. 트리거는
  상태 기계(pending→fired/expired)라 수명과 갱신 주기가 다르다. 섞으면 둘 다 흐려진다.

파일: $STOCK_DATA_DIR/tracking/triggers.csv

진입 조건의 출처(우선순위):
  1. reco["entry_trigger"]  — LLM이 스키마로 채운 구조화 값 (정답)
  2. parse_rule(entry_rule) — 산문에서 추출 (구버전 기록·LLM 누락 대비)
  3. 둘 다 없으면 immediate
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Any, Iterable, Optional

# horizon 별 트리거 유효기간(거래일). 이 안에 못 닿으면 expired
VALID_DAYS = {"단타": 3, "중단기": 7, "스윙": 20}
DEFAULT_VALID_DAYS = 5
# breakout 기본 폭 — '돌파 확인 후'처럼 수치가 없을 때
DEFAULT_BREAKOUT_PCT = 0.5


def triggers_csv() -> Path:
    from stock_auto.config.paths import tracking_dir
    return tracking_dir() / "triggers.csv"


@dataclass
class Trigger:
    trigger_id: str = ""
    signal_id: str = ""            # signals.csv 와 잇는 키
    date: str = ""                 # 추천 기준봉 날짜
    market: str = "US"
    symbol: str = ""
    # ── 조건 ──
    kind: str = "immediate"        # pullback | breakout | immediate
    value_pct: Optional[float] = None
    basis: str = "signal_close"    # signal_close | prev_close | open
    split: str = ""                # yes 면 분할 진입 권고
    basis_price: Optional[float] = None   # 등록 시점에 확정한 기준가
    target_price: Optional[float] = None  # 발화 가격
    rule_text: str = ""            # 사람이 읽는 원문
    source: str = "schema"         # schema | parsed | default
    # ── 추천 맥락(발화 시 Notion에 적는다) ──
    horizon: str = ""
    recommend: str = ""
    conviction: Optional[float] = None
    sector_etf: str = ""
    sector_label: str = ""
    effective_score: Optional[float] = None
    strategies: str = ""
    stop: Optional[float] = None
    target: Optional[float] = None
    rationale: str = ""
    # ── 상태 ──
    status: str = "pending"        # pending | fired | expired | cancelled
    created_at: str = ""
    fired_at: str = ""
    fired_price: Optional[float] = None
    notion_url: str = ""
    notify_at: str = ""

    def __post_init__(self):
        if not self.trigger_id:
            raw = f"{self.date}|{self.symbol}|{self.kind}|{self.value_pct}|{self.basis}"
            self.trigger_id = hashlib.sha1(raw.encode()).hexdigest()[:12]


FIELDNAMES = [f.name for f in fields(Trigger)]


# ── 산문 → 조건 파싱 ───────────────────────────────────────────────────────
# 숫자와 키워드 사이에 '위 ', '아래로 ', '이상 ', '정도 ' 같은 말이 끼므로
# 한글 3글자까지 건너뛴다. 더 넓히면 다른 문장의 숫자를 잘못 잡는다.
_GAP = r"\s*%?\s*(?:[가-힣]{1,3}\s*){0,2}"

# 숫자 — 쉼표도 소수점으로 받는다.
# 한글 입력에서 '-0,5' 처럼 쓰는 일이 흔한데, 쉼표를 모르면 정규식이 쉼표
# **뒤의 5** 를 숫자로 집어 0.5% 가 5% 가 된다(10배). 그러면 트리거가 사실상
# 영원히 발화하지 않는다 — 조용히 아무 일도 안 일어나는 종류의 실패다.
# 소수부를 1~2자리로 제한하고 뒤에 숫자가 더 오면 받지 않는다.
# 그래야 '1,500'(천 단위 구분) 을 1.50 으로 잘못 읽지 않는다.
_NUM = r"(\d+(?:[.,]\d{1,2}(?!\d))?)"

# 퍼센트로 말이 되는 상한. 이보다 크면 파싱 오류로 보고 경고한다.
MAX_SANE_PCT = 20.0

_PULLBACK = re.compile(
    r"(?:-\s*)?" + _NUM + _GAP + r"(?:눌림|눌리|조정|하락|되돌림|빠지)")
_BREAKOUT = re.compile(
    r"(?:\+\s*)?" + _NUM + _GAP + r"(?:돌파|상승|상회|뚫)")
_BREAKOUT_BARE = re.compile(r"돌파|상회|뚫고|신고가")
_SPLIT = re.compile(r"분할|나눠|절반|1/2|반씩")
_BASIS = (("prev_close", re.compile(r"전일\s*종가|전일종가|어제\s*종가")),
          ("open", re.compile(r"시가|개장")),
          ("signal_close", re.compile(r"종가|현재가")))


def _pct(raw: str, source: str = "") -> float:
    """'0,5' → 0.5. 말이 안 되는 값은 경고하고 0으로 돌려 immediate 로 떨어뜨린다.

    조용히 통과시키면 안 되는 이유: 값이 틀린 트리거는 발화하지 않고 만료될
    뿐이어서, 아무 일도 일어나지 않는 것과 구분이 안 된다.
    """
    try:
        v = float(raw.replace(",", "."))
    except (TypeError, ValueError):
        return 0.0
    if not (0 < v <= MAX_SANE_PCT):
        print(f"[trigger] ⚠️ 진입 조건의 % 값이 비정상({v}) — 조건을 무시합니다. "
              f"원문: {source[:80]!r}")
        return 0.0
    return v


def parse_rule(text: str) -> dict[str, Any]:
    """
    진입 조건 문장에서 기계용 조건을 뽑는다. 못 뽑으면 immediate.

    '시가 -1% 눌림 & RVOL>1.5'      → pullback 1.0 open
    '1.5% 눌림 시 분할 진입'         → pullback 1.5 signal_close split
    '종가 돌파 확인 후'              → breakout 0.5 signal_close
    '전일 종가 2% 위 돌파'           → breakout 2.0 prev_close
    """
    t = (text or "").strip()
    out: dict[str, Any] = {"kind": "immediate", "value_pct": 0.0,
                           "basis": "signal_close",
                           "split": bool(_SPLIT.search(t))}
    if not t:
        return out

    # 기준가 — 먼저 나오는 표현을 택한다
    for name, rx in _BASIS:
        if rx.search(t):
            out["basis"] = name
            break

    # % 값이 비정상이면 kind 를 바꾸지 않고 immediate 로 남긴다.
    # pullback 에 value_pct=0 을 두면 기준가에 닿는 순간 발화해버려 의도와
    # 정반대가 된다(눌림을 기다리라는 조건이 '지금 사라'가 된다).
    m = _PULLBACK.search(t)
    if m:
        v = _pct(m.group(1), t)
        if v > 0:
            out["kind"] = "pullback"
            out["value_pct"] = v
        return out

    m = _BREAKOUT.search(t)
    if m:
        v = _pct(m.group(1), t)
        if v > 0:
            out["kind"] = "breakout"
            out["value_pct"] = v
        return out

    if _BREAKOUT_BARE.search(t):
        out["kind"] = "breakout"
        out["value_pct"] = DEFAULT_BREAKOUT_PCT
        return out

    return out


def resolve_condition(reco: Optional[dict], rule_text: str = ""
                      ) -> tuple[dict[str, Any], str]:
    """(조건, 출처) — 스키마 값이 있으면 그것, 없으면 산문 파싱."""
    et = (reco or {}).get("entry_trigger")
    if isinstance(et, dict) and et.get("kind") in ("pullback", "breakout", "immediate"):
        try:
            return ({"kind": str(et["kind"]),
                     "value_pct": float(et.get("value_pct") or 0.0),
                     "basis": str(et.get("basis") or "signal_close"),
                     "split": bool(et.get("split"))}, "schema")
        except (TypeError, ValueError):
            pass
    text = rule_text or (reco or {}).get("entry_rule") or ""
    cond = parse_rule(text)
    return cond, ("parsed" if cond["kind"] != "immediate" else "default")


def target_price(kind: str, value_pct: Optional[float],
                 basis_price: Optional[float]) -> Optional[float]:
    if basis_price is None or basis_price <= 0:
        return None
    v = float(value_pct or 0.0)
    if kind == "pullback":
        return round(basis_price * (1.0 - v / 100.0), 4)
    if kind == "breakout":
        return round(basis_price * (1.0 + v / 100.0), 4)
    return round(basis_price, 4)      # immediate


def reached(kind: str, target: Optional[float], low: Optional[float],
            high: Optional[float]) -> bool:
    """도달 판정. pullback은 저가로, breakout은 고가로 본다."""
    if kind == "immediate":
        return True
    if target is None:
        return False
    if kind == "pullback":
        return low is not None and low <= target
    if kind == "breakout":
        return high is not None and high >= target
    return False


# ── 등록 ──────────────────────────────────────────────────────────────────
def from_recommendation(row: dict, reco: Optional[dict], date: str,
                        signal_id: str = "", sector_label: str = "",
                        prev_close: Optional[float] = None) -> Trigger:
    """screener 행 + LLM 추천 → Trigger 1건."""
    from stock_auto.config.clock import now_et
    reco = reco or {}
    cond, src = resolve_condition(reco, str(reco.get("entry_rule", "")))
    close = _f(row.get("close"))
    basis_price = {"signal_close": close, "prev_close": prev_close,
                   "open": close}.get(cond["basis"]) or close
    rat = reco.get("rationale") or {}
    rationale = " / ".join(
        f"{k}: {str(v)[:120]}" for k, v in rat.items() if v) if isinstance(rat, dict) else ""
    return Trigger(
        signal_id=signal_id, date=date,
        market=str(row.get("market", "US")),
        symbol=str(row.get("symbol") or reco.get("ticker") or ""),
        kind=cond["kind"], value_pct=cond["value_pct"], basis=cond["basis"],
        split="yes" if cond["split"] else "",
        basis_price=basis_price,
        target_price=target_price(cond["kind"], cond["value_pct"], basis_price),
        rule_text=str(reco.get("entry_rule", ""))[:300], source=src,
        horizon=str(reco.get("horizon", "")),
        recommend=str(reco.get("recommend", "")),
        conviction=_f(reco.get("conviction")),
        sector_etf=str(row.get("sector_etf", "")),
        sector_label=sector_label,
        effective_score=_f(row.get("effective_score")),
        strategies=str(row.get("strategies", "")),
        stop=_f(reco.get("stop")) if _f(reco.get("stop")) is not None else _f(row.get("stop")),
        target=_f((reco.get("targets") or [None])[0]) or _f(row.get("target")),
        rationale=rationale[:900],
        created_at=now_et().strftime("%Y-%m-%d %H:%M %Z"),
    )


# ── 저장소 ────────────────────────────────────────────────────────────────
def _path(base: Optional[str] = None) -> Path:
    return Path(base) if base else triggers_csv()


def migrate_header(base: Optional[str] = None) -> list[str]:
    """헤더가 FIELDNAMES와 다르면 전체 재작성. store.migrate_header와 같은 이유 —
    컬럼을 추가하면 append 가 쓴 새 컬럼이 짧은 헤더 때문에 조용히 안 읽힌다."""
    p = _path(base)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        head = next(csv.reader(f), [])
    if head == FIELDNAMES:
        return []
    rows = load(base)
    added = [c for c in FIELDNAMES if c not in head]
    save_all(rows, base)
    return added


def append(items: Iterable[Trigger], base: Optional[str] = None) -> int:
    """신규 트리거 적립. 동일 trigger_id는 건너뛴다."""
    p = _path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    added = migrate_header(base)
    if added:
        print(f"[trigger] CSV 헤더 갱신 — 컬럼 추가 {', '.join(added)}")
    existing = {r["trigger_id"] for r in load(base)} if p.exists() else set()
    new = [t for t in items if t.trigger_id not in existing]
    if not new:
        return 0
    write_header = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            w.writeheader()
        for t in new:
            w.writerow({k: ("" if v is None else v) for k, v in asdict(t).items()})
    return len(new)


def load(base: Optional[str] = None) -> list[dict[str, Any]]:
    p = _path(base)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_all(rows: list[dict[str, Any]], base: Optional[str] = None) -> None:
    p = _path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDNAMES})


def pending(base: Optional[str] = None, market: Optional[str] = None
            ) -> list[dict[str, Any]]:
    out = [r for r in load(base) if r.get("status") == "pending"]
    if market:
        out = [r for r in out if r.get("market") == market]
    return out


def pending_symbols(base: Optional[str] = None,
                    market: Optional[str] = None) -> list[str]:
    """실시간 감시 목록에 더할 종목 — watchlist가 참조한다."""
    seen: list[str] = []
    for r in pending(base, market):
        s = r.get("symbol")
        if s and s not in seen:
            seen.append(s)
    return seen


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _i(v) -> Optional[int]:
    x = _f(v)
    return None if x is None else int(x)
