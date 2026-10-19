"""
실측 기저율(base rate) — LLM에게 '추측' 대신 '데이터로부터 조정'을 시킨다.

왜 필요한가:
  conviction 은 지금까지 LLM이 아무 기준 없이 고르는 0~1 숫자였다. 프롬프트가
  0.6 이 무슨 뜻인지 말해준 적이 없으니 중간값에 몰리고(관측: 0.52·0.55·0.68),
  실현손익과의 관계도 측정되지 않았다.

  확률 추정의 기본은 **기저율에서 출발해 증거로 조정하는 것**이다. 이 시스템은
  이미 signals.csv 에 "Effective Score 구간별 실제 승률"을 갖고 있다. 그걸
  프롬프트에 넣어주면 LLM은 추측하는 대신 조정하게 된다.

  표본이 부족하면 **아무 숫자도 주지 않는다.** 12건에서 뽑은 승률을 기저율로
  주면 LLM이 그걸 근거로 확신을 세우고, 그게 과거 XGBoost 실패의 경로다
  (교훈 1번). 없으면 없다고 말하는 것이 틀린 숫자를 주는 것보다 낫다.
"""

from __future__ import annotations

from typing import Any, Optional

from stock_auto.tracking.store import _f

# report.py 의 SCORE_BINS 와 같은 구간을 쓴다 — 리포트에서 보는 표와
# 프롬프트에 들어가는 수치가 어긋나면 둘 중 뭘 믿어야 할지 알 수 없다.
SCORE_BINS: list[tuple[float, float]] = [
    (-99, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 5.0), (5.0, 6.0), (6.0, 99)]

MIN_BIN_N = 10        # 이 구간에서 기저율을 말하려면 최소 이만큼
MIN_TOTAL_N = 30      # 전체 기저율을 말하려면 최소 이만큼


def _closed(rows: list[dict]) -> list[dict]:
    """청산되어 승패가 확정된 건만."""
    out = []
    for r in rows:
        if str(r.get("exit_type", "")).strip() in ("", "nodata"):
            continue
        if _f(r.get("ret_pct")) is None:
            continue
        out.append(r)
    return out


def _band(score: Optional[float]) -> Optional[tuple[float, float]]:
    if score is None:
        return None
    for lo, hi in SCORE_BINS:
        if lo <= score < hi:
            return (lo, hi)
    return None


def win_rate_for(score: Optional[float],
                 rows: Optional[list[dict]] = None) -> Optional[dict]:
    """해당 Effective Score 구간의 실측 승률. 표본 미달이면 None.

    반환: {"band": (lo, hi), "n": int, "win_rate": float, "avg_ret": float}
    """
    if rows is None:
        from stock_auto.tracking import store
        rows = store.load()
    band = _band(score)
    if band is None:
        return None
    sel = [r for r in _closed(rows) if _band(_f(r.get("effective_score"))) == band]
    if len(sel) < MIN_BIN_N:
        return None
    rets = [_f(r.get("ret_pct")) for r in sel]
    rets = [x for x in rets if x is not None]
    if not rets:
        return None
    wins = sum(1 for x in rets if x > 0)
    return {"band": band, "n": len(rets), "win_rate": wins / len(rets),
            "avg_ret": sum(rets) / len(rets)}


def overall(rows: Optional[list[dict]] = None) -> Optional[dict]:
    """전체 실측 승률 — 구간 표본이 부족할 때의 차선."""
    if rows is None:
        from stock_auto.tracking import store
        rows = store.load()
    rets = [_f(r.get("ret_pct")) for r in _closed(rows)]
    rets = [x for x in rets if x is not None]
    if len(rets) < MIN_TOTAL_N:
        return None
    wins = sum(1 for x in rets if x > 0)
    return {"n": len(rets), "win_rate": wins / len(rets),
            "avg_ret": sum(rets) / len(rets)}


def prior_line(score: Optional[float],
               rows: Optional[list[dict]] = None) -> str:
    """프롬프트에 넣을 한 줄. 표본이 없으면 '없음'을 분명히 말한다.

    LLM 에게 '데이터가 없다'를 숨기면 LLM은 있는 줄 알고 자신감을 올린다.
    """
    if rows is None:
        from stock_auto.tracking import store
        try:
            rows = store.load()
        except Exception:  # noqa: BLE001 — 기저율 조회 실패가 추천을 막지 않게
            rows = []
    b = win_rate_for(score, rows)
    if b:
        lo, hi = b["band"]
        lo_s = "~" if lo <= -99 else f"{lo:.1f}"
        hi_s = "~" if hi >= 99 else f"{hi:.1f}"
        return (f"이 종목의 Effective {score} 는 [{lo_s}, {hi_s}) 구간이고, "
                f"그 구간의 **실측 승률은 {b['win_rate'] * 100:.0f}% "
                f"(표본 {b['n']}건, 평균 {b['avg_ret']:+.2f}%)** 다. "
                f"이 값에서 출발해 뉴스·수급·밸류 증거로 위아래로 조정하라.")
    o = overall(rows)
    if o:
        return (f"이 구간의 표본은 아직 부족하다. 전체 실측 승률은 "
                f"{o['win_rate'] * 100:.0f}%(표본 {o['n']}건)다. "
                f"이 값에서 출발해 조정하되 구간 특성은 반영되지 않았음을 감안하라.")
    return ("실측 기저율이 아직 없다(청산 표본 부족). 근거 없이 확신을 "
            "높이지 말고 0.5 근처에서 출발해, 증거가 분명한 만큼만 움직여라.")


def cluster_warning(convictions: list[float]) -> Optional[str]:
    """확신도가 좁은 구간에 뭉쳐 있으면 변별력이 없다 — 그 사실을 알린다."""
    vals = [v for v in convictions if v is not None]
    if len(vals) < 4:
        return None
    spread = max(vals) - min(vals)
    if spread <= 0.10:
        return (f"⚠️ 확신도가 {min(vals):.2f}~{max(vals):.2f} 로 거의 같습니다"
                f"(폭 {spread:.2f}) — 종목 간 변별력이 없어 선별·비중 조절에 "
                f"쓸 수 없습니다.")
    return None


def action_hint(conviction: Optional[float]) -> str:
    """확신도를 '그래서 뭘 다르게 하나'로 바꾼다. 지금까지 아무도 쓰지 않았다.

    주문 자동화가 없는 시스템이므로 금액이 아니라 **행동**으로 표현한다.
    """
    c = _f(conviction)
    if c is None:
        return ""
    if c >= 0.70:
        return "확신 높음 — 계획대로 진입"
    if c >= 0.55:
        return "확신 보통 — 분할 진입 권장"
    if c >= 0.45:
        return "확신 낮음 — 관망 또는 최소 비중"
    return "확신 매우 낮음 — 진입 보류 권장"
