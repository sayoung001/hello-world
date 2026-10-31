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

    tp, sl = _barriers(rec, entry, df[df.index <= sig_date])

    # 갭 통과 방어: 진입가가 이미 배리어를 넘어섰다면 조건부 진입이 성립하지 않는다.
    # (갭다운으로 손절선 아래에서 시작 / 갭업으로 목표가 위에서 시작)
    # 이를 체결로 간주하면 손절이 '수익'으로 기록되는 등 통계가 오염된다 → 미체결 처리.
    if (sl is not None and entry <= sl) or (tp is not None and entry >= tp):
        return Outcome(exit_type="skip",
                       exit_date=pd.Timestamp(fwd.index[0]).strftime("%Y-%m-%d"),
                       exit_price=round(entry, 4))

    n = hold_days or hold_days_for(rec)
    # 진입일(시가 진입) 봉부터 관찰한다. 예전에는 fwd.iloc[1:] 로 진입일을 건너뛰어
    # 진입 당일 장중에 손절선·목표가를 스친 것을 못 봤다 — 둘 다 과소 집계.
    path = fwd.iloc[0:n]
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

    # ★ 보유기간이 다 차지 않았으면 아직 결과가 없다 — timeout 이 아니다.
    #   예전에는 일봉이 하루치만 있어도(추천 다음날 아침) 그 하루를 '타임아웃'으로
    #   확정했다. 그래서 주간 요약이 "타임아웃 59건 · 평균 보유 1.0일" 이 됐고,
    #   한 번 라벨이 붙으면 다시 보지 않아 통계가 영구히 오염됐다.
    if len(path) < n:
        return Outcome(exit_type="nodata")

    last_ts = path.index[-1]
    last_close = float(path.iloc[-1]["Close"])
    return _mk("timeout", last_ts, last_close, entry, mae, mfe, len(path), -1)


def hold_days_for(rec: dict) -> int:
    return HOLD_DAYS.get(str(rec.get("horizon", "")), DEFAULT_HOLD)


def premature_timeouts(rows: list[dict]) -> list[dict]:
    """보유기간이 차기 전에 timeout 으로 확정된 행 — 재라벨 대상."""
    out = []
    for r in rows:
        if str(r.get("exit_type", "")) != "timeout":
            continue
        try:
            held = int(float(r.get("days_held") or 0))
        except (TypeError, ValueError):
            held = 0
        if held < hold_days_for(r):
            out.append(r)
    return out


_RESULT_FIELDS = ("labeled_at", "exit_type", "exit_date", "exit_price", "ret_pct",
                  "mae_pct", "mfe_pct", "days_held", "label", "direction_correct")


def reset_premature(rows: list[dict]) -> int:
    """조기 타임아웃 라벨을 지워 다시 라벨링되게 한다. 바뀐 행 수를 돌려준다.
    label_pending 이 매번 먼저 호출한다(멱등) — 과거에 잘못 붙은 라벨도 자연히 고쳐진다."""
    bad = premature_timeouts(rows)
    for r in bad:
        for k in _RESULT_FIELDS:
            r[k] = ""
    return len(bad)


def _barriers(rec: dict, entry: float, hist: Optional[pd.DataFrame] = None
              ) -> tuple[Optional[float], Optional[float]]:
    tp, sl = _f(rec.get("target")), _f(rec.get("stop"))
    if tp is None or sl is None:
        # 기록에 ATR이 없으면(폭주 알림 등) 신호일까지의 일봉에서 직접 산출한다.
        # 배리어가 없으면 전 건이 timeout(-1)이 되어 알림 정밀도를 측정할 수 없다.
        atr = _f(rec.get("atr")) or _atr_from_history(hist)
        if atr and atr > 0:
            tp = tp if tp is not None else entry + FALLBACK_TP_ATR * atr
            sl = sl if sl is not None else entry - FALLBACK_SL_ATR * atr
    return tp, sl


def _atr_from_history(hist: Optional[pd.DataFrame], period: int = 14
                      ) -> Optional[float]:
    """신호일까지의 일봉으로 ATR(단순평균 True Range) 산출. look-ahead 없음."""
    if hist is None or len(hist) < period + 1:
        return None
    h, l, pc = hist["High"], hist["Low"], hist["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    v = tr.tail(period).mean()
    return None if pd.isna(v) else float(v)


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
        if v is None or v == "":
            return None
        f = float(v)
        return None if f != f else f     # NaN도 '값 없음'
    except (TypeError, ValueError):
        return None


# ── 배치 실행 ──────────────────────────────────────────────────────────────
def label_pending(base: Optional[str] = None,
                  ohlcv_map: Optional[dict[str, pd.DataFrame]] = None,
                  lookback_days: int = 120) -> dict[str, int]:
    """
    미라벨 기록을 일괄 라벨링하고 저장. ohlcv_map 주입 시 다운로드 생략(테스트).
    반환: {'labeled': n, 'pending': n, 'nodata': n}
    """
    from stock_auto.tracking import store
    from stock_auto.config.settings import Market

    rows = store.load(base)
    n_reset = reset_premature(rows)
    if n_reset:
        print(f"[labeler] 조기 타임아웃 {n_reset}건 라벨 해제 — 보유기간이 찬 뒤 다시 판정")
    todo = [r for r in rows if not r.get("exit_type")]
    if not todo:
        if n_reset:
            store.save_all(rows, base)
        return {"labeled": 0, "pending": 0, "nodata": 0, "skip": 0, "reset": n_reset}

    # 종목별 일봉 확보
    if ohlcv_map is None:
        from datetime import timedelta
        from stock_auto.data.downloader import load_or_download
        ohlcv_map = {}
        # 종목별 시장 — 첫 기록의 시장을 전 종목에 쓰면 US/KR 혼재 시 다운로드가 어긋난다
        sym_market = {r["symbol"]: r.get("market") or "US" for r in todo}
        syms = sorted(sym_market)
        earliest = min(pd.Timestamp(r["date"]) for r in todo)
        # ATR(14) 폴백 산출을 위해 신호일 이전 여유분을 확보한다
        start = (earliest - timedelta(days=60)).strftime("%Y-%m-%d")
        for s in syms:
            mk = Market(sym_market[s])
            try:
                ohlcv_map[s] = load_or_download(s, start, None, mk, use_cache=False)
            except Exception as e:  # noqa: BLE001
                print(f"[labeler] {s} 일봉 실패: {type(e).__name__}: {e}")

    stat = {"labeled": 0, "pending": 0, "nodata": 0, "skip": 0, "reset": n_reset}
    # 라벨 시각도 거래소(ET) 기준 — 서버 TZ가 바뀌어도 감사 추적이 흔들리지 않게
    from stock_auto.config.clock import now_et
    now = now_et().strftime("%Y-%m-%d %H:%M %Z")
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
