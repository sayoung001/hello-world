"""
수익 vs 손실 추천의 공통점 — "돈이 된 추천은 무엇이 달랐나".

질문: 청산이 끝난 추천(BUY/WATCH, 게이트 통과)을 수익 난 것과 손실 난 것으로 나눴을 때,
      어느 특징이 **한쪽에 치우쳐** 나타나는가. 단순 승률표(report.py 캘리브레이션)와 다른 점은
      '손실 쪽과 비교해 수익 쪽에 유독 많은 특징'(lift)을 찾는 것이다.

방법(통계 패키지 없이, 작은 표본에 맞게):
  · 수치 피처는 전체 청산분의 3분위(하/중/상)로 자른다 — 구간 경계를 미리 정하지 않아
    표본이 적어도 각 구간에 비슷한 수가 들어간다.
  · 구간(또는 범주)마다 "수익 중 비중 / 손실 중 비중" = lift. 라플라스 보정(+0.5)으로
    0 나눗셈과 1~2건짜리 극단값을 누른다.
  · 특이 공통점 = 표본 ≥ min_n 이고 lift 가 1.5배 이상(또는 1/1.5 이하)인 구간.
    점수 |log2 lift|·√n 으로 정렬 — 치우침이 크고 건수도 있는 것이 위로.
  · 수치 피처는 AUC(수익 쪽 값이 손실 쪽 값보다 클 확률)도 함께 — 0.5 가 무차별.
  · 상위 수익 특징 2개를 **동시에** 만족한 추천의 승률을 따로 본다(조합 검증).
  · MAE/MFE/보유일/청산 유형은 '원인'이 아니라 '결과 양상'이므로 따로 적는다.

주의: 표본이 100~200건이면 lift 2배도 우연일 수 있다. 이 모듈은 가설을 **찍어 주는** 도구이고,
      확정은 표본이 쌓인 뒤 캘리브레이션(report.py)·게이트 실험으로 한다. 결과 문구에도
      건수를 항상 함께 적는다.

사용:
  .venv/bin/python -m stock_auto.tracking.attribution                  # 콘솔(마크다운)
  .venv/bin/python -m stock_auto.tracking.attribution --market US --days 90
  .venv/bin/python -m stock_auto.tracking.attribution --telegram        # 짧은 요약 전송
  .venv/bin/python -m stock_auto.tracking.attribution --notion          # Notion 페이지 게시
  .venv/bin/python -m stock_auto.tracking.attribution --out logs/attribution.md
주간 리포트(토 10:00 KST)가 같은 내용을 텔레그램·Notion 으로 보낸다(run_daemon._weekly_report).
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional

from stock_auto.tracking import pnl

MIN_N = 5                 # 특이 공통점으로 꼽는 최소 건수(한쪽 구간 전체)
LIFT_CUT = 1.5            # 이 배수 이상 치우쳐야 '특이'
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


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


# ── 1. 대상 추출 + 피처 파생 ───────────────────────────────────────────────
# (키, 표시 이름). 수치 피처는 3분위로, 범주 피처는 값 그대로 쓴다.
NUMERIC: list[tuple[str, str]] = [
    ("effective_score", "Effective 점수"),
    ("conviction", "LLM 확신도"),
    ("money_score", "자금 점수"),
    ("price_score", "가격 점수"),
    ("liquidity_score", "유동성 점수"),
    ("penalty", "감점"),
    ("rsi_14", "RSI(14)"),
    ("adx", "ADX"),
    ("rvol", "상대 거래량(RVOL)"),
    ("mom_rank", "모멘텀 순위(0~1)"),
    ("mom_12_1", "12-1 모멘텀"),
    ("atr_pct", "ATR/종가 %"),
    ("stop_pct", "손절 폭 %"),
    ("target_pct", "목표 폭 %"),
    ("rr", "손익비(목표/손절)"),
    ("p50_pct", "LLM 예상손익 P50 %"),
    ("news_count", "뉴스 헤드라인 수"),
    ("news_sentiment", "뉴스 센티먼트"),
    ("days_to_earnings", "실적 발표까지 일수"),
]
CATEGORICAL: list[tuple[str, str]] = [
    ("recommend", "추천 등급"),
    ("horizon", "보유기간"),
    ("entry_kind", "진입 조건 유형"),
    ("entry_split", "분할 진입"),
    ("trigger_status", "진입 조건 도달"),
    ("stock_regime", "종목 레짐"),
    ("macro_regime", "매크로 레짐"),
    ("sector_status", "섹터 상태 점수"),
    ("news_pick", "SAVE PICK 뉴스"),
    ("llm_stop", "LLM 손절 조정"),
    ("weekday", "추천 요일"),
    ("month", "추천 월"),
    ("market", "시장"),
    ("executed", "실행 표시"),
]
# 결과 쪽(원인이 아니라 양상) — 특이 공통점 후보에서 뺀다
OUTCOME_SIDE: list[tuple[str, str]] = [
    ("mae_pct", "최대 역행(MAE) %"),
    ("mfe_pct", "최대 순행(MFE) %"),
    ("days_held", "보유일"),
]
_KIND_KO = {"pullback": "눌림", "breakout": "돌파", "immediate": "즉시"}
_TRIG_KO = {"fired": "도달(발화)", "expired": "미도달(만료)", "cancelled": "무효(손절선)",
            "pending": "대기"}


def derive(r: dict, trig: Optional[dict] = None) -> dict[str, Any]:
    """signals.csv 한 행(+트리거 행) → 분석용 피처. 값이 없으면 None."""
    close, stop, target = _f(r.get("close")), _f(r.get("stop")), _f(r.get("target"))
    atr = _f(r.get("atr"))
    d: dict[str, Any] = {k: _f(r.get(k)) for k, _ in NUMERIC if k in r}
    d.update({k: _f(r.get(k)) for k, _ in OUTCOME_SIDE})
    d["atr_pct"] = (atr / close * 100) if (atr is not None and close) else None
    d["stop_pct"] = ((close - stop) / close * 100) if (close and stop is not None) else None
    d["target_pct"] = ((target - close) / close * 100) if (close and target is not None) else None
    rr = _f(r.get("rr_ratio"))
    if rr is None and d["stop_pct"] and d["target_pct"] is not None and d["stop_pct"] > 0:
        rr = d["target_pct"] / d["stop_pct"]
    d["rr"] = rr
    d["p50_pct"] = _f(r.get("p50_pct"))

    d["recommend"] = str(r.get("recommend") or "").upper() or None
    d["horizon"] = str(r.get("horizon") or "") or None
    d["market"] = str(r.get("market") or "") or None
    d["executed"] = {"yes": "샀다", "no": "안 샀다", "skip": "보류"}.get(str(r.get("executed") or ""), "미입력")
    sr, mr, ss = _i(r.get("stock_regime")), _i(r.get("macro_regime")), _i(r.get("sector_status_score"))
    d["stock_regime"] = f"R{sr}" if sr is not None else None
    d["macro_regime"] = f"L{mr}" if mr is not None else None
    d["sector_status"] = f"섹터 {ss}" if ss is not None else None
    npk = _i(r.get("news_pick"))
    d["news_pick"] = None if npk is None else ("있음" if npk else "없음")
    # 진입 조건 유형 — 트리거 행이 있으면 그 kind(스키마), 없으면 산문 파싱
    kind, split = None, None
    if trig:
        kind = str(trig.get("kind") or "") or None
        split = str(trig.get("split") or "").lower() == "yes"
        d["trigger_status"] = _TRIG_KO.get(str(trig.get("status") or ""), None)
    else:
        d["trigger_status"] = None
    if kind is None:
        try:
            from stock_auto.tracking.triggers import parse_rule
            p = parse_rule(str(r.get("entry_rule") or ""))
            kind, split = p["kind"], bool(p.get("split"))
        except Exception:  # noqa: BLE001
            kind, split = None, None
    d["entry_kind"] = _KIND_KO.get(kind or "", None)
    d["entry_split"] = None if split is None else ("분할" if split else "일괄")
    # LLM 이 규칙엔진 손절을 바꿨나
    stop_rule = _f(r.get("stop_rule"))
    if stop is not None and stop_rule is not None and close:
        diff = (stop - stop_rule) / close * 100
        d["llm_stop"] = "유지" if abs(diff) < 0.05 else ("좁힘(손절 올림)" if diff > 0 else "넓힘(손절 내림)")
    else:
        d["llm_stop"] = None
    try:
        dt = datetime.strptime(str(r.get("date") or "")[:10], "%Y-%m-%d")
        d["weekday"] = WEEKDAYS[dt.weekday()]
        d["month"] = dt.strftime("%Y-%m")
    except ValueError:
        d["weekday"] = d["month"] = None
    d["ret_pct"] = _f(r.get("ret_pct"))
    d["exit_type"] = str(r.get("exit_type") or "")
    d["symbol"], d["date"] = r.get("symbol"), r.get("date")
    return d


def closed_recommendations(rows: list[dict], triggers: Optional[list[dict]] = None,
                           market: Optional[str] = None, days: Optional[int] = None,
                           include_gated: bool = False) -> list[dict[str, Any]]:
    """청산 완료(tp/sl/timeout) + 수익률 있는 추천 → 피처 행. 수익률 0 은 어느 쪽도 아니라 뺀다."""
    tmap = {t.get("signal_id"): t for t in (triggers or []) if t.get("signal_id")}
    cutoff = None
    if days:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for r in rows:
        if include_gated:
            ok = (r.get("source") == "batch"
                  and str(r.get("recommend") or "").upper() in pnl.RECOMMENDED)
        else:
            ok = pnl.is_recommendation(r)
        if not ok or r.get("exit_type") not in ("tp", "sl", "timeout"):
            continue
        if market and str(r.get("market") or "").upper() != market.upper():
            continue
        if cutoff and str(r.get("date") or "") < cutoff:
            continue
        ret = _f(r.get("ret_pct"))
        if ret is None or ret == 0:
            continue
        out.append(derive(r, tmap.get(r.get("signal_id"))))
    return out


# ── 2. 통계 ───────────────────────────────────────────────────────────────
def _quantiles(vals: list[float]) -> tuple[float, float]:
    s = sorted(vals)
    n = len(s)
    q1 = s[max(0, min(n - 1, int(round((n - 1) / 3))))]
    q2 = s[max(0, min(n - 1, int(round(2 * (n - 1) / 3))))]
    return q1, q2


def _fmt_num(x: float) -> str:
    return f"{x:.0f}" if abs(x) >= 100 else (f"{x:.1f}" if abs(x) >= 10 else f"{x:.2f}")


def _bucket_numeric(trades: list[dict], key: str
                    ) -> dict[str, tuple[list[dict], Optional[float], Optional[float]]]:
    """3분위 구간 → {라벨: (행들, lo, hi)}. 구간은 lo < v ≤ hi (lo/hi None 은 열린 끝)."""
    vals = [t[key] for t in trades if t.get(key) is not None]
    if len(vals) < 6 or len(set(vals)) < 3:
        return {}
    q1, q2 = _quantiles(vals)
    if q1 == q2:
        return {}
    spec = [(f"하(≤{_fmt_num(q1)})", None, q1), (f"중({_fmt_num(q1)}~{_fmt_num(q2)})", q1, q2),
            (f"상(>{_fmt_num(q2)})", q2, None)]
    b = {lab: ([], lo, hi) for lab, lo, hi in spec}
    for t in trades:
        v = t.get(key)
        if v is None:
            continue
        lab = spec[0][0] if v <= q1 else spec[1][0] if v <= q2 else spec[2][0]
        b[lab][0].append(t)
    return b


def _bucket_categorical(trades: list[dict], key: str) -> dict[str, list[dict]]:
    b: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        v = t.get(key)
        if v is not None and v != "":
            b[str(v)].append(t)
    return dict(b)


def _group_stats(sub: list[dict], W: int, L: int) -> dict[str, Any]:
    wins = [t for t in sub if t["ret_pct"] > 0]
    losses = [t for t in sub if t["ret_pct"] < 0]
    n = len(sub)
    gw = sum(t["ret_pct"] for t in wins)
    gl = abs(sum(t["ret_pct"] for t in losses))
    share_w = (len(wins) + 0.5) / (W + 1)
    share_l = (len(losses) + 0.5) / (L + 1)
    lift = share_w / share_l
    return {
        "n": n, "n_win": len(wins), "n_loss": len(losses),
        "win_rate": (len(wins) / n * 100) if n else 0.0,
        "avg_ret": (sum(t["ret_pct"] for t in sub) / n) if n else 0.0,
        "pf": (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0),
        "share_w": share_w * 100, "share_l": share_l * 100, "lift": lift,
        "score": abs(math.log2(lift)) * math.sqrt(n) if n else 0.0,
    }


def _auc(w: list[float], l: list[float]) -> Optional[float]:
    """P(수익 쪽 값 > 손실 쪽 값) — 동률 0.5. 표본이 작아 O(n²)로 충분."""
    if not w or not l:
        return None
    s = 0.0
    for a in w:
        for b in l:
            s += 1.0 if a > b else 0.5 if a == b else 0.0
    return s / (len(w) * len(l))


def _median(v: list[float]) -> float:
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def analyze(rows: list[dict], triggers: Optional[list[dict]] = None,
            market: Optional[str] = None, days: Optional[int] = None,
            min_n: int = MIN_N, include_gated: bool = False) -> dict[str, Any]:
    """전체 분석 → dict. markdown()/telegram_text() 가 글로 만든다."""
    if triggers is None:
        try:
            from stock_auto.tracking import triggers as trig_store
            triggers = trig_store.load()
        except Exception:  # noqa: BLE001
            triggers = []
    trades = closed_recommendations(rows, triggers, market, days, include_gated)
    wins = [t for t in trades if t["ret_pct"] > 0]
    losses = [t for t in trades if t["ret_pct"] < 0]
    W, L = len(wins), len(losses)
    res: dict[str, Any] = {
        "market": market or "ALL", "days": days, "min_n": min_n,
        "n": len(trades), "n_win": W, "n_loss": L,
        "win_rate": (W / len(trades) * 100) if trades else 0.0,
        "avg_win": (sum(t["ret_pct"] for t in wins) / W) if W else 0.0,
        "avg_loss": (sum(t["ret_pct"] for t in losses) / L) if L else 0.0,
        "factors": [], "numeric": [], "outcome": [], "top_win": [], "top_loss": [],
        "combo_win": None, "combo_loss": None, "exit_mix": {},
    }
    if W < 3 or L < 3:
        res["note"] = (f"수익 {W}건 · 손실 {L}건 — 양쪽 3건 이상 쌓여야 비교할 수 있습니다. "
                       "min_n·기간을 조정하거나 더 기다리세요.")
        return res

    # 청산 유형 구성(결과 양상)
    for side, grp in (("win", wins), ("loss", losses)):
        res["exit_mix"][side] = {k: sum(1 for t in grp if t["exit_type"] == k)
                                 for k in ("tp", "sl", "timeout")}

    # 구간별 lift
    labels = dict(NUMERIC + CATEGORICAL)
    for key, _ in NUMERIC:
        for bucket, (sub, lo, hi) in _bucket_numeric(trades, key).items():
            st = _group_stats(sub, W, L)
            st.update({"factor": key, "label": labels[key], "bucket": bucket, "numeric": True,
                       "lo": lo, "hi": hi})
            res["factors"].append(st)
    for key, _ in CATEGORICAL:
        bk = _bucket_categorical(trades, key)
        if len(bk) < 2:
            continue   # 전부 같은 값이면 비교가 안 된다
        for bucket, sub in bk.items():
            st = _group_stats(sub, W, L)
            st.update({"factor": key, "label": labels[key], "bucket": bucket, "numeric": False})
            res["factors"].append(st)

    # 수치 피처 — 평균·중앙값·AUC
    for key, label in NUMERIC + OUTCOME_SIDE:
        vw = [t[key] for t in wins if t.get(key) is not None]
        vl = [t[key] for t in losses if t.get(key) is not None]
        if len(vw) < 3 or len(vl) < 3:
            continue
        row = {"factor": key, "label": label, "n_w": len(vw), "n_l": len(vl),
               "mean_w": sum(vw) / len(vw), "mean_l": sum(vl) / len(vl),
               "med_w": _median(vw), "med_l": _median(vl), "auc": _auc(vw, vl)}
        (res["outcome"] if key in dict(OUTCOME_SIDE) else res["numeric"]).append(row)
    res["numeric"].sort(key=lambda x: -abs((x["auc"] or 0.5) - 0.5))

    # 특이 공통점 — 치우침 큰 구간. 한 피처당 한 구간만(같은 피처의 반대쪽 구간은 중복 정보)
    def _pick(side: str) -> list[dict]:
        cands = [f for f in res["factors"] if f["n"] >= min_n
                 and ((f["lift"] >= LIFT_CUT) if side == "win" else (f["lift"] <= 1 / LIFT_CUT))]
        cands.sort(key=lambda f: -f["score"])
        seen, out = set(), []
        for f in cands:
            if f["factor"] in seen:
                continue
            seen.add(f["factor"])
            out.append(f)
        return out[:8]
    res["top_win"], res["top_loss"] = _pick("win"), _pick("loss")

    # 조합 검증 — 상위 두 특징을 동시에 만족하는 추천
    def _combo(top: list[dict]) -> Optional[dict]:
        if len(top) < 2:
            return None
        a, b = top[0], top[1]
        sub = [t for t in trades if _in_bucket(t, a) and _in_bucket(t, b)]
        if not sub:
            return None
        st = _group_stats(sub, W, L)
        st["desc"] = f"{a['label']}={a['bucket']} 그리고 {b['label']}={b['bucket']}"
        return st
    res["combo_win"], res["combo_loss"] = _combo(res["top_win"]), _combo(res["top_loss"])
    return res


def _in_bucket(t: dict, f: dict) -> bool:
    v = t.get(f["factor"])
    if v is None:
        return False
    if not f["numeric"]:
        return str(v) == f["bucket"]
    lo, hi = f.get("lo"), f.get("hi")     # 라벨은 반올림돼 있으니 원래 경계로 판정
    return (lo is None or v > lo) and (hi is None or v <= hi)


# ── 3. 글 ─────────────────────────────────────────────────────────────────
def _pf(v: float) -> str:
    return "∞" if v == float("inf") else f"{v:.2f}"


def _lift(v: float) -> str:
    return f"{v:.1f}배" if v >= 1 else f"1/{1 / v:.1f}배"


def _head(res: dict) -> list[str]:
    scope = res["market"] + (f" · 최근 {res['days']}일" if res.get("days") else "")
    return [f"청산 완료 추천 {res['n']}건({scope}) — 수익 {res['n_win']}건 · 손실 {res['n_loss']}건 · "
            f"승률 {res['win_rate']:.0f}%",
            f"평균 수익 {res['avg_win']:+.2f}% / 평균 손실 {res['avg_loss']:+.2f}%"]


def _trait_line(f: dict, side: str) -> str:
    other = "손실" if side == "win" else "수익"
    mine = "수익" if side == "win" else "손실"
    return (f"{f['label']} = {f['bucket']} — {f['n']}건, 승률 {f['win_rate']:.0f}%, 평균 {f['avg_ret']:+.2f}% "
            f"({mine} 중 {f['share_w' if side == 'win' else 'share_l']:.0f}% vs {other} 중 "
            f"{f['share_l' if side == 'win' else 'share_w']:.0f}%, {_lift(f['lift'] if side == 'win' else 1 / f['lift'])})")


def telegram_text(res: dict) -> str:
    """텔레그램용 짧은 요약(평문). 핵심 특징 각 4개 + 조합 + 결과 양상."""
    L = ["🔎 수익 vs 손실 추천 공통점"] + _head(res)
    if res.get("note"):
        return "\n".join(L + [res["note"]])
    if res["top_win"]:
        L.append("")
        L.append("✅ 수익 쪽에 몰린 특징")
        L += [f"· {_trait_line(f, 'win')}" for f in res["top_win"][:4]]
    if res["top_loss"]:
        L.append("")
        L.append("❌ 손실 쪽에 몰린 특징")
        L += [f"· {_trait_line(f, 'loss')}" for f in res["top_loss"][:4]]
    if not res["top_win"] and not res["top_loss"]:
        L.append(f"\n{LIFT_CUT}배 이상 치우친 특징이 없습니다(min_n={res['min_n']}). "
                 "수익과 손실이 특징상 구분되지 않음 — 점수·게이트가 결과를 설명하지 못하는 상태.")
    cw, cl = res.get("combo_win"), res.get("combo_loss")
    if cw or cl:
        L.append("")
        L.append("🧩 조합 검증(두 특징 동시 만족)")
        if cw:
            L.append(f"· 수익 특징 2개: {cw['n']}건 승률 {cw['win_rate']:.0f}% 평균 {cw['avg_ret']:+.2f}% PF {_pf(cw['pf'])}")
        if cl:
            L.append(f"· 손실 특징 2개: {cl['n']}건 승률 {cl['win_rate']:.0f}% 평균 {cl['avg_ret']:+.2f}%")
    em = res.get("exit_mix") or {}
    if em:
        w, l = em.get("win", {}), em.get("loss", {})
        L.append("")
        L.append(f"📐 결과 양상: 수익 = TP {w.get('tp', 0)} / 타임아웃 {w.get('timeout', 0)} · "
                 f"손실 = SL {l.get('sl', 0)} / 타임아웃 {l.get('timeout', 0)}")
    for o in res.get("outcome", []):
        L.append(f"· {o['label']}: 수익 평균 {o['mean_w']:+.2f} vs 손실 {o['mean_l']:+.2f}")
    strong = [x for x in res.get("numeric", []) if x["auc"] is not None and abs(x["auc"] - 0.5) >= 0.12][:4]
    if strong:
        L.append("")
        L.append("📊 값 차이가 큰 수치(AUC, 0.5=무차별)")
        L += [f"· {x['label']}: 수익 중앙값 {x['med_w']:.2f} vs 손실 {x['med_l']:.2f} (AUC {x['auc']:.2f})"
              for x in strong]
    L.append("")
    L.append(f"※ 표본 {res['n']}건 기준 가설입니다. 전체 표: PC 화면 성과 탭 / Notion '[성과] 추천 요인 분석'")
    return "\n".join(L)


def markdown(res: dict) -> str:
    """전체 표가 담긴 마크다운(Notion·파일용)."""
    L = ["# 수익 vs 손실 추천 공통점 분석", ""] + [f"- {x}" for x in _head(res)] + [""]
    if res.get("note"):
        return "\n".join(L + [f"> {res['note']}", ""])
    L += ["## 1. 특이 공통점", "",
          f"수익 중 비중과 손실 중 비중이 {LIFT_CUT}배 이상 다른 구간(건수 ≥ {res['min_n']}). "
          "한 피처당 가장 치우친 구간 하나.", ""]
    for side, title in (("win", "### 수익 쪽에 몰린 특징"), ("loss", "### 손실 쪽에 몰린 특징")):
        top = res["top_win" if side == "win" else "top_loss"]
        L += [title, ""]
        if not top:
            L += ["_해당 없음_", ""]
            continue
        L += ["| 특징 | 구간 | 건수 | 승률 | 평균수익 | PF | 수익 중 | 손실 중 | 치우침 |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
        for f in top:
            L.append(f"| {f['label']} | {f['bucket']} | {f['n']} | {f['win_rate']:.0f}% | "
                     f"{f['avg_ret']:+.2f}% | {_pf(f['pf'])} | {f['share_w']:.0f}% | {f['share_l']:.0f}% | "
                     f"{_lift(f['lift'])} |")
        L.append("")
    for key, title in (("combo_win", "수익 특징 2개 동시 만족"), ("combo_loss", "손실 특징 2개 동시 만족")):
        c = res.get(key)
        if c:
            L += [f"**{title}** — {c['desc']}: {c['n']}건 · 승률 {c['win_rate']:.0f}% · "
                  f"평균 {c['avg_ret']:+.2f}% · PF {_pf(c['pf'])}", ""]

    L += ["## 2. 수치 피처 — 수익 vs 손실 분포", "",
          "AUC = 수익 쪽 값이 손실 쪽 값보다 클 확률. 0.5 가 무차별, 0.65↑ 또는 0.35↓ 면 눈여겨볼 것.", "",
          "| 피처 | 수익 평균 | 손실 평균 | 수익 중앙값 | 손실 중앙값 | AUC | n(수익/손실) |",
          "|---|---:|---:|---:|---:|---:|---|"]
    for x in res["numeric"]:
        L.append(f"| {x['label']} | {x['mean_w']:.2f} | {x['mean_l']:.2f} | {x['med_w']:.2f} | "
                 f"{x['med_l']:.2f} | {x['auc']:.2f} | {x['n_w']}/{x['n_l']} |")
    L.append("")

    L += ["## 3. 결과 양상(원인 아님)", ""]
    em = res.get("exit_mix") or {}
    if em:
        w, l = em.get("win", {}), em.get("loss", {})
        L += [f"- 수익: TP {w.get('tp', 0)} · 타임아웃 {w.get('timeout', 0)}",
              f"- 손실: SL {l.get('sl', 0)} · 타임아웃 {l.get('timeout', 0)}"]
    for o in res["outcome"]:
        L.append(f"- {o['label']}: 수익 평균 {o['mean_w']:+.2f} / 손실 평균 {o['mean_l']:+.2f} (AUC {o['auc']:.2f})")
    L.append("")

    L += ["## 4. 전체 구간표", "",
          "| 특징 | 구간 | 건수 | 승률 | 평균수익 | PF | 치우침 |", "|---|---|---:|---:|---:|---:|---:|"]
    for f in sorted(res["factors"], key=lambda f: (f["label"], f["bucket"])):
        L.append(f"| {f['label']} | {f['bucket']} | {f['n']} | {f['win_rate']:.0f}% | "
                 f"{f['avg_ret']:+.2f}% | {_pf(f['pf'])} | {_lift(f['lift'])} |")
    L += ["", f"> 표본 {res['n']}건 기준. 치우침은 라플라스 보정(+0.5)된 '수익 중 비중 / 손실 중 비중'.", ""]
    return "\n".join(L)


# ── 4. CLI ────────────────────────────────────────────────────────────────
def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    from stock_auto.config.env import load_env, get_secrets
    from stock_auto.tracking import store
    ap = argparse.ArgumentParser(description="수익 vs 손실 추천 공통점 분석")
    ap.add_argument("--market", default=None, help="US | KR (기본 전체)")
    ap.add_argument("--days", type=int, default=None, help="최근 N일 추천만")
    ap.add_argument("--min-n", type=int, default=MIN_N)
    ap.add_argument("--include-gated", action="store_true", help="게이트 차단분(BUY/WATCH)도 포함")
    ap.add_argument("--telegram", action="store_true", help="짧은 요약을 텔레그램으로")
    ap.add_argument("--notion", action="store_true", help="전체 표를 Notion 페이지로")
    ap.add_argument("--out", default=None, help="마크다운 저장 경로")
    ap.add_argument("--short", action="store_true", help="콘솔에 텔레그램용 요약만")
    a = ap.parse_args(argv)
    load_env()
    rows = store.load()
    res = analyze(rows, market=a.market, days=a.days, min_n=a.min_n, include_gated=a.include_gated)
    md, short = markdown(res), telegram_text(res)
    print(short if a.short else md)
    if a.out:
        from pathlib import Path
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(md, encoding="utf-8")
        print(f"\n→ {a.out}")
    if a.telegram or a.notion:
        sec = get_secrets()
        if a.telegram:
            from stock_auto.notify.telegram import Telegram
            tg = Telegram(sec.telegram_bot_token, sec.telegram_chat_id)
            print("텔레그램 전송 " + ("완료" if tg.enabled and tg.send_message(short, parse_mode="") else "실패/미설정"))
        if a.notion:
            if not (sec.has_notion and sec.notion_parent_page_id):
                print("Notion 미설정(NOTION_TOKEN / NOTION_PARENT_PAGE_ID)")
            else:
                from stock_auto.integrations.notion_publisher import NotionPublisher
                pid = NotionPublisher(token=sec.notion_token).create_page_from_markdown(
                    sec.notion_parent_page_id,
                    f"[성과] 추천 요인 분석 ({datetime.now():%Y-%m-%d})" + (f" {a.market}" if a.market else ""), md)
                print("Notion 게시 " + ("완료" if pid else "실패"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
