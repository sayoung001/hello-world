"""
결과 라벨러 — 삼중 배리어(Triple-Barrier)로 모든 신호에 결과를 붙인다.

배리어:
  상단 = target(TP), 하단 = stop(SL), 수직 = 보유기간 상한(거래일)
  먼저 닿는 것이 라벨. 같은 날 둘 다 닿으면 보수적으로 SL 우선(최악 가정).

진입 가정:
  신호일 종가가 아니라 '다음 거래일 시가'로 진입한다. 신호일 종가 진입은
  실현 불가능한 look-ahead이며 성과를 체계적으로 부풀린다.

TP/SL이 기록에 없으면 ATR 배수로 대체(전략 기본값과 동일한 사상).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import pandas as pd

# 보유기간 상한(거래일) — 추천 horizon별
HOLD_DAYS = {"단타": 3, "중단기": 7, "스윙": 20}
DEFAULT_HOLD = 5
# 기록에 TP/SL이 없을 때 ATR 배수 폴백
FALLBACK_TP_ATR = 2.0
FALLBACK_SL_ATR = 1.2


@dataclass
class Outcome:
    exit_type: str            # tp | sl | timeout | nodata
    exit_date: str = ""
    exit_price: Optional[float] = None
    ret_pct: Optional[float] = None
    mae_pct: Optional[float] = None
    mfe_pct: Optional[float] = None
    days_held: Optional[int] = None
    label: Optional[int] = None            # 1=TP, 0=SL, -1=timeout
    direction_correct: Optional[int] = None


def label_record(rec: dict[str, Any], df: pd.DataFrame,
                 hold_days: Optional[int] = None) -> Outcome:
    """
    rec: store.SignalRecord를 dict로 읽은 행
    df : 해당 종목 일봉 (DatetimeIndex, Open/High/Low/Close)
    """
    if df is None or df.empty:
        return Outcome(exit_type="nodata")

    sig_date = pd.Timestamp(rec["date"])
    fwd = df[df.index > sig_date]
    if len(fwd) < 2:
        return Outcome(exit_type="nodata")   # 아직 결과가 안 나옴

    entry = float(fwd.iloc[0]["Open"])       # 다음 거래일 시가 진입
    if entry <= 0:
        return Outcome(exit_type="nodata")

    tp, sl = _barriers(rec, entry)

    # 갭 통과 방어: 진입가가 이미 배리어를 넘어섰다면 조건부 진입이 성립하지 않는다.
    # (갭다운으로 손절선 아래에서 시작 / 갭업으로 목표가 위에서 시작)
    # 이를 체결로 간주하면 손절이 '수익'으로 기록되는 등 통계가 오염된다 → 미체결 처리.
    if (sl is not None and entry <= sl) or (tp is not None and entry >= tp):
        return Outcome(exit_type="skip",
                       exit_date=pd.Timestamp(fwd.index[0]).strftime("%Y-%m-%d"),
                       exit_price=round(entry, 4))

    n = hold_days or HOLD_DAYS.get(str(rec.get("horizon", "")), DEFAULT_HOLD)
    path = fwd.iloc[1:n + 1]                 # 진입 다음 봉부터 관찰
    if path.empty:
        return Outcome(exit_type="nodata")

    mae = mfe = 0.0
    for i, (ts, bar) in enumerate(path.iterrows(), start=1):
        hi, lo = float(bar["High"]), float(bar["Low"])
        mfe = max(mfe, (hi - entry) / entry * 100.0)
        mae = min(mae, (lo - entry) / entry * 100.0)

        hit_sl = sl is not None and lo <= sl
        hit_tp = tp is not None and hi >= tp
        if hit_sl:                            # 보수적: 동시 도달 시 SL 우선
            return _mk("sl", ts, sl, entry, mae, mfe, i, 0)
        if hit_tp:
            return _mk("tp", ts, tp, entry, mae, mfe, i, 1)

    last_ts = path.index[-1]
    last_close = float(path.iloc[-1]["Close"])
    return _mk("timeout", last_ts, last_close, entry, mae, mfe, len(path), -1)


def _barriers(rec: dict, entry: float) -> tuple[Optional[float], Optional[float]]:
    tp, sl = _f(rec.get("target")), _f(rec.get("stop"))
    if tp is None or sl is None:
        atr = _f(rec.get("atr"))
        if atr and atr > 0:
            tp = tp if tp is not None else entry + FALLBACK_TP_ATR * atr
            sl = sl if sl is not None else entry - FALLBACK_SL_ATR * atr
    return tp, sl


def _mk(kind: str, ts, price: float, entry: float,
        mae: float, mfe: float, days: int, label: int) -> Outcome:
    ret = (price - entry) / entry * 100.0
    return Outcome(
        exit_type=kind,
        exit_date=pd.Timestamp(ts).strftime("%Y-%m-%d"),
        exit_price=round(price, 4),
        ret_pct=round(ret, 3),
        mae_pct=round(mae, 3),
        mfe_pct=round(mfe, 3),
        days_held=days,
        label=label,
        # 방향이 맞았는가: 순행이 역행보다 컸는가 (SL로 끝났어도 판정 가능)
        direction_correct=1 if mfe >= abs(mae) else 0,
    )


def _f(v) -> Optional[float]:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


# ── 배치 실행 ──────────────────────────────────────────────────────────────
def label_pending(base: str = "data/tracking/signals.csv",
                  ohlcv_map: Optional[dict[str, pd.DataFrame]] = None,
                  lookback_days: int = 120) -> dict[str, int]:
    """
    미라벨 기록을 일괄 라벨링하고 저장. ohlcv_map 주입 시 다운로드 생략(테스트).
    반환: {'labeled': n, 'pending': n, 'nodata': n}
    """
    from stock_auto.tracking import store
    from stock_auto.config.settings import Market

    rows = store.load(base)
    todo = [r for r in rows if not r.get("exit_type")]
    if not todo:
        return {"labeled": 0, "pending": 0, "nodata": 0}

    # 종목별 일봉 확보
    if ohlcv_map is None:
        from datetime import timedelta
        from stock_auto.data.downloader import load_or_download
        ohlcv_map = {}
        syms = sorted({r["symbol"] for r in todo})
        earliest = min(pd.Timestamp(r["date"]) for r in todo)
        start = (earliest - timedelta(days=10)).strftime("%Y-%m-%d")
        for s in syms:
            mk = Market(todo[0].get("market", "US"))
            try:
                ohlcv_map[s] = load_or_download(s, start, None, mk, use_cache=False)
            except Exception as e:  # noqa: BLE001
                print(f"[labeler] {s} 일봉 실패: {type(e).__name__}: {e}")

    stat = {"labeled": 0, "pending": 0, "nodata": 0, "skip": 0}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    for r in todo:
        out = label_record(r, ohlcv_map.get(r["symbol"]))
        if out.exit_type == "nodata":
            stat["pending" if r["symbol"] in ohlcv_map else "nodata"] += 1
            continue
        r["labeled_at"] = now
        for k, v in out.__dict__.items():
            r[k] = "" if v is None else v
        stat["skip" if out.exit_type == "skip" else "labeled"] += 1

    store.save_all(rows, base)
    return stat
