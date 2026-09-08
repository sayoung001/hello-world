"""
신호 기록 저장소 — 모든 추천·알림을 결과 라벨링 대상으로 적립한다.

설계 원칙(선택 편향 방지):
  실행하지 않은 추천도 반드시 기록한다. "안 샀으니 기록 안 함"은 데이터를
  체계적으로 오염시킨다. 라벨링은 실행 여부와 무관하게 전 건에 붙인다.

파일: data/tracking/signals.csv  (단일 파일, source 컬럼으로 배치/알림 구분)
  - 기록 시점에 FEATURE_FIELDS까지 함께 남겨야 나중에 캘리브레이션 학습이 가능하다.
  - 결과(OUTCOME_FIELDS)는 labeler가 나중에 채운다.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import Any, Iterable, Optional

from stock_auto.config.settings import Market

def default_path() -> str:
    """신호 CSV 경로. 호출 시점에 STOCK_DATA_DIR을 반영한다."""
    from stock_auto.config.paths import signals_csv
    return str(signals_csv())



@dataclass
class SignalRecord:
    """추천 1건 또는 알림 1건. 결과 필드는 라벨링 후 채워진다."""
    # ── 식별 ──
    signal_id: str = ""
    date: str = ""                 # 거래소 기준 거래일 (ET)
    market: str = "US"
    symbol: str = ""
    source: str = "batch"          # batch(일일추천) | alert(폭주알림)
    window_min: Optional[int] = None   # alert일 때 발화 윈도우

    # ── 판단 ──
    recommend: str = ""            # BUY / WATCH / AVOID
    horizon: str = ""              # 단타 / 중단기 / 스윙
    conviction: Optional[float] = None
    executed: str = ""             # ""(미분류) | yes | no | skip  ← 트리아지 입력
    executed_at: str = ""          # 트리아지 입력 시각(ET)
    triage_note: str = ""          # 사람이 남긴 한 줄 사유
    # 게이트에 막힌 신호도 기록한다(선택 편향·검증 공백 방지).
    # "" = 통과 / "macro" / "sector" / "earnings" (복수는 쉼표 구분)
    gated_by: str = ""
    days_to_earnings: Optional[int] = None

    # ── 피처 (캘리브레이션 학습용) ──
    close: Optional[float] = None
    effective_score: Optional[float] = None
    money_score: Optional[float] = None
    price_score: Optional[float] = None
    liquidity_score: Optional[float] = None
    penalty: Optional[float] = None
    stock_regime: Optional[int] = None
    macro_regime: Optional[int] = None
    sector_status_score: Optional[int] = None
    rsi_14: Optional[float] = None
    adx: Optional[float] = None
    rvol: Optional[float] = None
    mom_rank: Optional[float] = None          # 횡단면 모멘텀 순위 (0~1)
    mom_12_1: Optional[float] = None
    atr: Optional[float] = None

    # ── 계획 ──
    entry_rule: str = ""
    stop: Optional[float] = None
    target: Optional[float] = None
    rr_ratio: Optional[float] = None
    p10_pct: Optional[float] = None
    p50_pct: Optional[float] = None
    p90_pct: Optional[float] = None

    # ── 결과 (labeler가 채움) ──
    labeled_at: str = ""
    exit_type: str = ""            # tp | sl | timeout | nodata
    exit_date: str = ""
    exit_price: Optional[float] = None
    ret_pct: Optional[float] = None
    mae_pct: Optional[float] = None    # 최대 역행 (Maximum Adverse Excursion)
    mfe_pct: Optional[float] = None    # 최대 순행 (Maximum Favorable Excursion)
    days_held: Optional[int] = None
    label: Optional[int] = None        # 1=TP선도달, 0=SL선도달, -1=타임아웃
    direction_correct: Optional[int] = None

    def __post_init__(self):
        if not self.signal_id:
            self.signal_id = make_id(self.date, self.symbol, self.source,
                                     self.window_min)


FIELDNAMES = [f.name for f in fields(SignalRecord)]
OUTCOME_FIELDS = ["labeled_at", "exit_type", "exit_date", "exit_price", "ret_pct",
                  "mae_pct", "mfe_pct", "days_held", "label", "direction_correct"]


def make_id(date: str, symbol: str, source: str,
            window: Optional[int] = None) -> str:
    raw = f"{date}|{symbol}|{source}|{window if window is not None else ''}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


# ── 입출력 ────────────────────────────────────────────────────────────────
def _path(base: Optional[str]) -> Path:
    return Path(base or default_path())


def append(records: Iterable[SignalRecord], base: Optional[str] = None) -> int:
    """신규 기록 추가. 동일 signal_id는 건너뛴다(중복 방지)."""
    p = _path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = {r["signal_id"] for r in load(base)} if p.exists() else set()
    new = [r for r in records if r.signal_id not in existing]
    if not new:
        return 0
    write_header = not p.exists()
    with p.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            w.writeheader()
        for r in new:
            w.writerow({k: ("" if v is None else v) for k, v in asdict(r).items()})
    return len(new)


def load(base: Optional[str] = None) -> list[dict[str, Any]]:
    p = _path(base)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_all(rows: list[dict[str, Any]], base: Optional[str] = None) -> None:
    """전체 재작성(라벨 갱신용)."""
    p = _path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDNAMES})


def pending(base: Optional[str] = None) -> list[dict[str, Any]]:
    """아직 라벨이 붙지 않은 기록."""
    return [r for r in load(base) if not r.get("exit_type")]


EXECUTED_VALUES = ("yes", "no", "skip")


def set_executed(signal_id: str, executed: str, note: str = "",
                 base: Optional[str] = None) -> bool:
    """트리아지 입력(실행/미실행/보류) 반영. 입력 시각과 사유도 함께 남긴다."""
    if executed not in EXECUTED_VALUES:
        raise ValueError(f"executed는 {EXECUTED_VALUES} 중 하나여야 합니다: {executed!r}")
    from stock_auto.config.clock import now_et
    rows = load(base)
    hit = False
    for r in rows:
        if r.get("signal_id") == signal_id:
            r["executed"] = executed
            r["executed_at"] = now_et().strftime("%Y-%m-%d %H:%M %Z")
            if note:
                r["triage_note"] = note
            hit = True
    if hit:
        save_all(rows, base)
    return hit


def set_executed_many(updates: dict[str, tuple[str, str]],
                      base: Optional[str] = None) -> int:
    """{signal_id: (executed, note)} 를 한 번에 반영 — CSV를 1회만 다시 쓴다."""
    from stock_auto.config.clock import now_et
    for ex, _ in updates.values():
        if ex not in EXECUTED_VALUES:
            raise ValueError(f"executed는 {EXECUTED_VALUES} 중 하나여야 합니다: {ex!r}")
    rows = load(base)
    stamp = now_et().strftime("%Y-%m-%d %H:%M %Z")
    n = 0
    for r in rows:
        u = updates.get(r.get("signal_id", ""))
        if u:
            r["executed"], note = u[0], u[1]
            r["executed_at"] = stamp
            if note:
                r["triage_note"] = note
            n += 1
    if n:
        save_all(rows, base)
    return n


def untriaged(base: Optional[str] = None, source: Optional[str] = "batch",
              include_gated: bool = False) -> list[dict[str, Any]]:
    """
    아직 실행 여부가 입력되지 않은 기록.

    기본적으로 게이트에 막힌 신호는 제외한다 — 사람에게 보여준 적이 없는 신호에
    "샀냐"고 묻는 것은 의미가 없다.
    """
    out = []
    for r in load(base):
        if r.get("executed"):
            continue
        if source and r.get("source") != source:
            continue
        if not include_gated and r.get("gated_by"):
            continue
        out.append(r)
    return out


# ── 상위 파이프라인에서 기록 만들기 ────────────────────────────────────────
def from_screen_row(row: dict, date: str, reco: Optional[dict] = None
                    ) -> SignalRecord:
    """screener 행(+선택적 LLM 추천)을 기록으로 변환."""
    pnl = (reco or {}).get("expected_pnl_today") or {}
    targets = (reco or {}).get("targets") or []
    return SignalRecord(
        date=date, market=str(row.get("market", "US")),
        symbol=str(row.get("symbol", "")), source="batch",
        gated_by=str(row.get("gated_by", "") or ""),
        days_to_earnings=_i(row.get("days_to_earnings")),
        recommend=str((reco or {}).get("recommend", "")),
        # LLM 추천이 없는 기록(게이트 차단분)은 규칙엔진의 styles를 보유기간으로 쓴다.
        # 비우면 라벨러가 DEFAULT_HOLD(5일)로 대체해 통과분(단타 3일)과 보유기간이
        # 달라지고, '게이트 통과 vs 차단' 비교가 서로 다른 조건의 비교가 된다.
        horizon=(str((reco or {}).get("horizon", ""))
                 or _style_horizon(row.get("styles"))),
        conviction=_f((reco or {}).get("conviction")),
        close=_f(row.get("close")),
        effective_score=_f(row.get("effective_score")),
        money_score=_f(row.get("money_score")),
        price_score=_f(row.get("price_score")),
        liquidity_score=_f(row.get("liquidity_score")),
        penalty=_f(row.get("penalty")),
        stock_regime=_i(row.get("stock_regime")),
        macro_regime=_i(row.get("macro_regime")),
        sector_status_score=_i(row.get("sector_status_score")),
        rsi_14=_f(row.get("rsi_14")), adx=_f(row.get("adx")),
        rvol=_f(row.get("rvol")),
        mom_rank=_f(row.get("mom_rank")), mom_12_1=_f(row.get("mom_12_1")),
        atr=_f(row.get("atr")),
        entry_rule=str((reco or {}).get("entry_rule", "")),
        # 키가 있어도 값이 None/빈값이면 규칙엔진 값으로 폴백해야 한다
        # (dict.get(key, default)는 키가 존재하면 default를 쓰지 않는다)
        stop=_f((reco or {}).get("stop")) if _f((reco or {}).get("stop")) is not None
             else _f(row.get("stop")),
        target=_f(targets[0]) if targets and _f(targets[0]) is not None
               else _f(row.get("target")),
        rr_ratio=_f(row.get("rr_ratio")),
        p10_pct=_f(pnl.get("p10_pct")), p50_pct=_f(pnl.get("p50_pct")),
        p90_pct=_f(pnl.get("p90_pct")),
    )


def from_surge_signal(sig, date: str) -> SignalRecord:
    """폭주 알림(SurgeSignal)을 기록으로 변환."""
    m = sig.metrics
    return SignalRecord(
        date=date, market=sig.market.value, symbol=sig.symbol,
        source="alert", window_min=sig.window_min,
        recommend="ALERT", close=_f(sig.price),
        rvol=_f(m.rvol), liquidity_score=None,
    )


_STYLE_ORDER = ("단타", "중단기", "스윙")


def _style_horizon(styles: Any) -> str:
    """screener의 styles('단타,중단기') → 보유기간 1개. 가장 짧은 쪽을 택한다(보수적)."""
    if not styles:
        return ""
    parts = {s.strip() for s in str(styles).split(",") if s.strip()}
    return next((s for s in _STYLE_ORDER if s in parts), "")


def _f(v) -> Optional[float]:
    """숫자 변환. NaN도 '값 없음'으로 본다 — pandas 컬럼에 None이 섞이면 NaN이 된다."""
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return None if f != f else f     # NaN 체크
    except (TypeError, ValueError):
        return None


def _i(v) -> Optional[int]:
    f = _f(v)
    return None if f is None else int(f)
