"""
실적 발표일 캘린더 — 발표 전후 진입 차단 (금융분석 문서 4-1).

왜 필요한가:
  실적 발표 갭은 **손절을 건너뛴다.** 손절 3.2%를 걸어놨는데 -15% 갭다운으로 열리면
  실제 손실은 3.2%가 아니라 15%다. 그런데 삼중 배리어 라벨러는 이런 건을 `skip`으로
  처리하므로 **리포트 성과에서는 사라지고 실계좌에서만 발생한다.**

  게다가 이 시스템의 주력 신호인 MoneyFlowSurge(매수건의 87.8%에서 발동)는 자금 유입
  신호다. 실적 발표 직전 포지셔닝은 전형적인 자금 유입이므로 **구조적으로 실적
  이벤트에 끌린다.** 필터가 없으면 가장 위험한 구간을 가장 자주 사게 된다.

정책:
  - 발표일까지 D-BLOCK_DAYS(기본 3거래일) 이내 → 신규 진입 차단
  - 발표 당일 포함, 발표 직후 D+1도 차단(갭 다음날 변동성)
  - 캘린더 조회 실패 종목은 **차단하지 않는다**(정보 없음 ≠ 위험). 대신 집계해 보고한다.

데이터:
  yfinance `Ticker.calendar` / `Ticker.get_earnings_dates()`. 하루 1회 수집해 CSV 캐시.
  유니버스 500종목이면 수집에 수 분 걸리므로 배치 전 1회만 돌린다.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

CACHE_DIR = Path(os.environ.get("STOCK_DATA_DIR", "data")) / "earnings"
CACHE_FILE = CACHE_DIR / "earnings_calendar.csv"

# 진입 차단 구간 (거래일 아님 — 달력일 기준. 보수적으로 잡는다)
BLOCK_DAYS_BEFORE = 3     # 발표 D-3 ~ D 까지 차단
BLOCK_DAYS_AFTER = 1      # 발표 다음날까지 차단
# 캐시 유효기간 — 실적 일정은 자주 바뀌지 않지만 확정일로 갱신된다
CACHE_MAX_AGE_DAYS = 7


@dataclass(frozen=True)
class EarningsInfo:
    symbol: str
    next_date: Optional[date]      # 다음 실적 발표 예정일
    fetched_at: str = ""

    def days_until(self, today: date) -> Optional[int]:
        return None if self.next_date is None else (self.next_date - today).days

    def blocked(self, today: date) -> bool:
        """진입 차단 여부. 일정 미상이면 차단하지 않는다."""
        d = self.days_until(today)
        if d is None:
            return False
        return -BLOCK_DAYS_AFTER <= d <= BLOCK_DAYS_BEFORE


# ── 수집 ──────────────────────────────────────────────────────────────────
def fetch_one(symbol: str) -> Optional[date]:
    """yfinance에서 다음 실적 발표 예정일 1건. 실패 시 None."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        # 1차: calendar (dict 또는 DataFrame)
        cal = getattr(t, "calendar", None)
        d = _extract_from_calendar(cal)
        if d is not None:
            return d
        # 2차: get_earnings_dates — 미래 일정 중 가장 가까운 것
        try:
            df = t.get_earnings_dates(limit=8)
        except Exception:  # noqa: BLE001
            df = None
        if df is not None and len(df) > 0:
            today = date.today()
            future = sorted({_to_date(i) for i in df.index} - {None})
            for x in future:
                if x >= today:
                    return x
    except Exception:  # noqa: BLE001 — 종목 단위 실패는 전체를 막지 않는다
        return None
    return None


def _extract_from_calendar(cal) -> Optional[date]:
    if cal is None:
        return None
    # dict 형태: {'Earnings Date': [datetime.date, ...], ...}
    if isinstance(cal, dict):
        v = cal.get("Earnings Date") or cal.get("earningsDate")
        if isinstance(v, (list, tuple)) and v:
            return _to_date(v[0])
        return _to_date(v)
    # DataFrame 형태: index에 'Earnings Date'
    try:
        if "Earnings Date" in getattr(cal, "index", []):
            return _to_date(cal.loc["Earnings Date"].iloc[0])
    except Exception:  # noqa: BLE001
        pass
    return None


def _to_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        import pandas as pd
        ts = pd.Timestamp(v)
        return None if ts is None or ts is pd.NaT else ts.date()
    except Exception:  # noqa: BLE001
        return None


def refresh(symbols: Iterable[str], force: bool = False,
            path: Path = CACHE_FILE) -> dict[str, EarningsInfo]:
    """
    유니버스 전 종목의 실적 일정을 갱신해 CSV로 저장.

    force=False면 캐시가 CACHE_MAX_AGE_DAYS 이내인 종목은 건너뛴다.
    반환: {symbol: EarningsInfo}
    """
    from stock_auto.config.clock import now_et
    symbols = list(symbols)
    cached = load(path)
    today = now_et().date()
    stale: list[str] = []
    for s in symbols:
        info = cached.get(s)
        if force or info is None or _age_days(info.fetched_at, today) > CACHE_MAX_AGE_DAYS:
            stale.append(s)

    if not stale:
        print(f"[earnings] 캐시 최신 — {len(cached)}종목 (갱신 불필요)")
        return cached

    print(f"[earnings] {len(stale)}종목 실적 일정 수집 중...")
    ok = fail = 0
    stamp = now_et().strftime("%Y-%m-%d")
    for s in stale:
        d = fetch_one(s)
        cached[s] = EarningsInfo(symbol=s, next_date=d, fetched_at=stamp)
        if d is None:
            fail += 1
        else:
            ok += 1
    save(cached, path)
    print(f"[earnings] 수집 완료 — 일정 확인 {ok}건 · 미상 {fail}건 "
          f"(미상은 차단하지 않음)")
    return cached


def _age_days(fetched_at: str, today: date) -> int:
    d = _to_date(fetched_at)
    return 9999 if d is None else (today - d).days


# ── 캐시 입출력 ───────────────────────────────────────────────────────────
def save(data: dict[str, EarningsInfo], path: Path = CACHE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "next_date", "fetched_at"])
        for s in sorted(data):
            i = data[s]
            w.writerow([s, i.next_date.isoformat() if i.next_date else "",
                        i.fetched_at])


def load(path: Path = CACHE_FILE) -> dict[str, EarningsInfo]:
    if not path.exists():
        return {}
    out: dict[str, EarningsInfo] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["symbol"]] = EarningsInfo(
                symbol=r["symbol"], next_date=_to_date(r.get("next_date") or None),
                fetched_at=r.get("fetched_at", ""))
    return out


# ── 스크리너가 쓰는 형태 ───────────────────────────────────────────────────
def blocked_map(symbols: Iterable[str], today: Optional[date] = None,
                path: Path = CACHE_FILE) -> tuple[dict[str, bool], dict[str, Optional[int]]]:
    """
    {symbol: 차단여부}, {symbol: 발표까지 남은 일수} 반환.
    캐시가 없으면 전부 (False, None) — 정보 없음은 차단 사유가 아니다.
    """
    from stock_auto.config.clock import now_et
    today = today or now_et().date()
    cal = load(path)
    blocked: dict[str, bool] = {}
    days: dict[str, Optional[int]] = {}
    for s in symbols:
        info = cal.get(s)
        if info is None:
            blocked[s], days[s] = False, None
            continue
        blocked[s] = info.blocked(today)
        days[s] = info.days_until(today)
    return blocked, days


def summary_line(blocked: dict[str, bool], days: dict[str, Optional[int]]) -> str:
    n_block = sum(1 for v in blocked.values() if v)
    n_known = sum(1 for v in days.values() if v is not None)
    soon = sorted((d, s) for s, d in days.items()
                  if d is not None and 0 <= d <= 7)[:5]
    tail = (" · 임박: " + ", ".join(f"{s} D-{d}" for d, s in soon)) if soon else ""
    return (f"실적 게이트 — 차단 {n_block}종목 / 일정 확인 {n_known}종목"
            f" / 미상 {len(days) - n_known}종목{tail}")
