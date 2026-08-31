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

DEFAULT_PATH = "data/tracking/signals.csv"


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
    executed: str = ""             # ""(미분류) | yes | no  ← 트리아지 입력

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
def _path(base: str) -> Path:
    return Path(base)


def append(records: Iterable[SignalRecord], base: str = DEFAULT_PATH) -> int:
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


def load(base: str = DEFAULT_PATH) -> list[dict[str, Any]]:
    p = _path(base)
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_all(rows: list[dict[str, Any]], base: str = DEFAULT_PATH) -> None:
    """전체 재작성(라벨 갱신용)."""
    p = _path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDNAMES})


def pending(base: str = DEFAULT_PATH) -> list[dict[str, Any]]:
    """아직 라벨이 붙지 않은 기록."""
    return [r for r in load(base) if not r.get("exit_type")]


def set_executed(signal_id: str, executed: str,
                 base: str = DEFAULT_PATH) -> bool:
    """트리아지 입력(실행/보류/무시) 반영."""
    rows = load(base)
    hit = False
    for r in rows:
        if r.get("signal_id") == signal_id:
            r["executed"] = executed
            hit = True
    if hit:
        save_all(rows, base)
    return hit


# ── 상위 파이프라인에서 기록 만들기 ────────────────────────────────────────
def from_screen_row(row: dict, date: str, reco: Optional[dict] = None
                    ) -> SignalRecord:
    """screener 행(+선택적 LLM 추천)을 기록으로 변환."""
    pnl = (reco or {}).get("expected_pnl_today") or {}
    targets = (reco or {}).get("targets") or []
    return SignalRecord(
        date=date, market=str(row.get("market", "US")),
        symbol=str(row.get("symbol", "")), source="batch",
        recommend=str((reco or {}).get("recommend", "")),
        horizon=str((reco or {}).get("horizon", "")),
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
        stop=_f((reco or {}).get("stop", row.get("stop"))),
        target=_f(targets[0] if targets else row.get("target")),
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


def _f(v) -> Optional[float]:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _i(v) -> Optional[int]:
    f = _f(v)
    return None if f is None else int(f)
