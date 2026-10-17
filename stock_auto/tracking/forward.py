"""
보유일별 수익 곡선 — 추천 이후 1·2·3·5·10·15·20 거래일 시점의 수익률을 전부 기록한다.

삼중배리어(labeler)는 "먼저 닿은 것"만 남겨서 추천이 그 뒤로 어떻게 흘렀는지 보이지 않는다.
이 모듈은 배리어와 무관하게 '그냥 들고 있었으면' 수익률을 시점별로 남긴다.
→ "며칠째가 평균적으로 가장 좋았나" = 데이터로 정하는 최적 보유일.

진입 가정은 labeler와 동일: 신호일 **다음 거래일 시가**.
파일: $STOCK_DATA_DIR/tracking/forward_returns.csv  (signals.csv 스키마는 건드리지 않는다)
  signal_id, date, symbol, entry, r1 … r20 (%), mfe20, mae20, best_day, updated_at
아직 도달하지 않은 시점은 빈칸 — 매일 돌리면 채워진다(멱등).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Optional

import pandas as pd

HORIZONS = (1, 2, 3, 5, 10, 15, 20)
FIELDS = (["signal_id", "date", "symbol", "market", "recommend", "horizon", "gated_by",
           "executed", "entry"]
          + [f"r{h}" for h in HORIZONS]
          + ["mfe20", "mae20", "best_day", "n_days", "updated_at"])


def default_path() -> Path:
    from stock_auto.config.paths import tracking_dir
    return tracking_dir() / "forward_returns.csv"


def load(path: Optional[str] = None) -> list[dict[str, Any]]:
    p = Path(path or default_path())
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save(rows: list[dict[str, Any]], path: Optional[str] = None) -> None:
    p = Path(path or default_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})


def compute_row(rec: dict, df: Optional[pd.DataFrame]) -> Optional[dict[str, Any]]:
    """signals.csv 한 행 + 종목 일봉 → forward 행. 진입 불가(다음 봉 없음)면 None."""
    if df is None or df.empty:
        return None
    sig = pd.Timestamp(rec["date"])
    fwd = df[df.index > sig]
    if fwd.empty:
        return None
    entry = float(fwd.iloc[0]["Open"])
    if entry <= 0:
        return None
    path = fwd.iloc[1:]                    # 진입 다음 봉부터 관찰 (labeler와 동일)
    out: dict[str, Any] = {k: rec.get(k, "") for k in
                           ("signal_id", "date", "symbol", "market", "recommend",
                            "horizon", "gated_by", "executed")}
    out["entry"] = round(entry, 4)
    closes = path["Close"].astype(float).values
    for h in HORIZONS:
        out[f"r{h}"] = (round((closes[h - 1] / entry - 1) * 100, 3)
                        if len(closes) >= h else "")
    w = path.iloc[:20]
    if not w.empty:
        out["mfe20"] = round((float(w["High"].max()) / entry - 1) * 100, 3)
        out["mae20"] = round((float(w["Low"].min()) / entry - 1) * 100, 3)
        cl = w["Close"].astype(float).values
        out["best_day"] = int(cl.argmax()) + 1        # 종가 기준 최고점 도달일
    out["n_days"] = int(min(len(path), 20))
    return out


def update(signal_rows: Optional[list[dict]] = None,
           ohlcv_map: Optional[dict[str, pd.DataFrame]] = None,
           path: Optional[str] = None) -> dict[str, int]:
    """
    signals.csv 전 건에 대해 forward 행을 갱신한다. 20일이 다 찬 행은 건너뛴다.
    ohlcv_map 주입 시 다운로드 생략(테스트).
    """
    from stock_auto.tracking import store
    rows = signal_rows if signal_rows is not None else store.load()
    existing = {r["signal_id"]: r for r in load(path)}
    todo = [r for r in rows
            if not (existing.get(r["signal_id"], {}).get("n_days") in ("20", 20))]
    if not todo:
        return {"updated": 0, "complete": len(existing), "pending": 0}

    if ohlcv_map is None:
        from datetime import timedelta
        from stock_auto.config.settings import Market
        from stock_auto.data.downloader import load_or_download
        ohlcv_map = {}
        sym_market = {r["symbol"]: r.get("market") or "US" for r in todo}
        earliest = min(pd.Timestamp(r["date"]) for r in todo)
        start = (earliest - timedelta(days=10)).strftime("%Y-%m-%d")
        for s in sorted(sym_market):
            try:
                ohlcv_map[s] = load_or_download(s, start, None, Market(sym_market[s]))
            except Exception as e:  # noqa: BLE001
                print(f"[forward] {s} 일봉 실패: {type(e).__name__}: {e}")

    from stock_auto.config.clock import now_et
    stamp = now_et().strftime("%Y-%m-%d %H:%M %Z")
    n_upd = n_pending = 0
    for r in todo:
        row = compute_row(r, ohlcv_map.get(r["symbol"]))
        if row is None:
            n_pending += 1
            continue
        row["updated_at"] = stamp
        existing[row["signal_id"]] = row
        n_upd += 1
    save(list(existing.values()), path)
    complete = sum(1 for r in existing.values() if str(r.get("n_days")) == "20")
    return {"updated": n_upd, "complete": complete, "pending": n_pending}


# ── 집계 ──────────────────────────────────────────────────────────────────
def _f(v) -> Optional[float]:
    try:
        if v in (None, ""):
            return None
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def curve(rows: list[dict], only_recommended: bool = True) -> list[dict[str, Any]]:
    """시점별 평균수익·중앙값·승률·표본수. only_recommended=True면 게이트 차단분 제외."""
    sel = [r for r in rows if not (only_recommended and r.get("gated_by"))]
    out = []
    for h in HORIZONS:
        vals = [v for v in (_f(r.get(f"r{h}")) for r in sel) if v is not None]
        if not vals:
            out.append({"day": h, "n": 0})
            continue
        s = sorted(vals)
        out.append({"day": h, "n": len(vals),
                    "avg": sum(vals) / len(vals),
                    "median": s[len(s) // 2],
                    "win_rate": sum(1 for v in vals if v > 0) / len(vals) * 100})
    return out


def best_horizon(cv: list[dict]) -> Optional[dict]:
    """평균수익이 가장 높은 시점(표본 5건 이상만)."""
    cands = [c for c in cv if c.get("n", 0) >= 5]
    return max(cands, key=lambda c: c["avg"]) if cands else None


def section_md(rows: list[dict]) -> list[str]:
    """리포트에 붙일 Markdown 섹션."""
    cv = curve(rows)
    L = ["## 8. 보유일별 수익 곡선 (배리어 무관 · 다음날 시가 진입 · 종가 기준)", "",
         "| 보유일 | 표본 | 평균수익 | 중앙값 | 승률 |", "|---:|---:|---:|---:|---:|"]
    any_row = False
    for c in cv:
        if c.get("n", 0) == 0:
            continue
        any_row = True
        L.append(f"| D+{c['day']} | {c['n']} | {c['avg']:+.2f}% | "
                 f"{c['median']:+.2f}% | {c['win_rate']:.0f}% |")
    if not any_row:
        L.append("| _데이터 없음_ | | | | |")
    L.append("")
    b = best_horizon(cv)
    if b:
        L.append(f"- 평균수익 최고 시점 **D+{b['day']}** ({b['avg']:+.2f}%, 표본 {b['n']}건)"
                 " → 현재 데이터가 가리키는 보유일")
    bd = [int(r["best_day"]) for r in rows if str(r.get("best_day", "")).isdigit()]
    if bd:
        bd.sort()
        L.append(f"- 개별 종목 최고점 도달일 중앙값 **D+{bd[len(bd) // 2]}** "
                 f"(평균 {sum(bd) / len(bd):.1f}일, {len(bd)}건)")
    if not b:
        L.append("- 표본 5건 미만 — 최적 보유일 판단은 보류 (수집 계속)")
    L.append("")
    return L
