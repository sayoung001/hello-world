"""
LLM 계층 평가 — "LLM이 가치를 더하는가"를 기존 기록만으로 측정한다.

왜 필요한가:
  실측 24건/청산 12건에서 손익 -6.67%(승률 42%, PF 0.67)가 나왔는데,
  **같은 12건을 D+2까지 단순 보유하면 +27%(승률 73%)** 였다. 즉 LLM이 만든
  진입·청산 계획이 '그냥 들고 있기'보다 못했을 가능성이 있다. 표본 12건으로는
  결론을 못 내지만, 이 질문을 측정하지 않은 채로 모델을 늘리거나 provider를
  추가하면 **가치를 못 더하는 계층을 배로 늘리는 것**이 된다.

  과거 교훈 1번(XGBoost: Walk-Forward 없이 검증해 백테스트에서만 좋았던 모델)과
  같은 함정이다. 그래서 이 모듈은 표본이 부족하면 **숫자를 내지 않고 부족하다고
  말한다.** 작은 표본에서 나온 상관계수를 보여주는 것이 아무것도 안 보여주는
  것보다 나쁘다.

측정 3가지 (모두 signals.csv 46컬럼으로 계산 가능 — 새 수집 불필요):
  1. conviction ↔ 실현손익   — 확신도가 변별력을 갖는가. 없으면 장식이다.
  2. p50 예측 ↔ 실제         — LLM 자기 예측의 편향·정확도·구간 적중률
  3. 실현손익 ↔ MFE          — 청산이 너무 이른가(D+2 +27% 괴리의 후보 원인)
  4. LLM 손절·목표 ↔ 규칙엔진 — stop_rule/target_rule 컬럼이 쌓인 뒤부터 가능

사용:
  .venv/bin/python -m stock_auto.tracking.run_llm_eval
"""

from __future__ import annotations

from typing import Any, Optional

from stock_auto.tracking.store import _f

# ── 표본 기준 ──
# 작은 표본에서 상관·승률을 보여주면 그걸 근거로 판단하게 된다. 기준 미달이면
# 숫자 대신 "표본 부족"을 출력한다.
MIN_TOTAL = 20          # 어떤 결론이든 내보려면 최소 이만큼
MIN_BIN = 5             # 구간별 표를 한 줄이라도 보여주려면
MIN_CORR = 30           # 상관계수를 숫자로 말하려면

CONVICTION_BINS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 1.01)]
HORIZONS = ("단타", "중단기", "스윙")


# ──────────────────────────────────────────────────────────────────────────
# 통계 보조 — scipy 없이(2GB VM에 numpy/pandas 외 추가 설치 없음)
# ──────────────────────────────────────────────────────────────────────────
def _ranks(xs: list[float]) -> list[float]:
    """동순위는 평균 순위로(Spearman 표준)."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(xs: list[float], ys: list[float]) -> Optional[float]:
    """순위상관. 선형이 아니어도 '커지면 커지는' 관계를 잡는다."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx)
    dy = sum((b - my) ** 2 for b in ry)
    if dx <= 0 or dy <= 0:
        return None
    return num / (dx * dy) ** 0.5


def critical_rho(n: int, z: float = 1.96) -> float:
    """표본 n에서 '우연히' 나올 수 있는 |ρ| 의 95% 경계.

    ρ 의 귀무분포는 표준편차 ≈ 1/√(n-1) 이다(실측 확인: n=60 에서 0.133,
    이론 0.130). 이 경계를 넘지 않는 ρ 는 관계가 없다는 뜻이 아니라
    **현재 표본으로는 구분할 수 없다**는 뜻이다. 둘을 구분해서 말해야 한다.
    """
    return z / max(n - 1, 1) ** 0.5


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _pf(rets: list[float]) -> Optional[float]:
    """Profit Factor = 이익합 / |손실합|. 손실이 없으면 None(무한대 표기 회피)."""
    win = sum(r for r in rets if r > 0)
    loss = sum(r for r in rets if r < 0)
    return None if loss == 0 else win / abs(loss)


def _outcome_rows(rows: list[dict]) -> list[dict]:
    """라벨링이 끝나고 LLM 추천이 붙은 행만. 평가 대상의 정의다."""
    out = []
    for r in rows:
        if not str(r.get("exit_type", "")).strip():
            continue                            # 아직 청산 안 됨
        if str(r.get("source", "")) != "batch":
            continue                            # 폭주 알림은 LLM을 안 거친다
        if not str(r.get("recommend", "")).strip():
            continue                            # 게이트 차단분 등 LLM 미적용
        if _f(r.get("ret_pct")) is None:
            continue
        out.append(r)
    return out


def _stat_line(rets: list[float]) -> str:
    n = len(rets)
    wins = sum(1 for x in rets if x > 0)
    avg = sum(rets) / n
    pf = _pf(rets)
    pf_s = "-" if pf is None else f"{pf:.2f}"
    return f"n={n:<4} 승률 {wins / n * 100:5.1f}%  평균 {avg:+6.2f}%  PF {pf_s}"


# ──────────────────────────────────────────────────────────────────────────
# 1) conviction 변별력
# ──────────────────────────────────────────────────────────────────────────
def conviction_section(rows: list[dict]) -> list[str]:
    L = ["", "━━ 1. 확신도(conviction)가 변별력을 갖는가 ━━"]
    data = [(c, r) for r in rows
            if (c := _f(r.get("conviction"))) is not None]
    if len(data) < MIN_TOTAL:
        L.append(f"  표본 부족 — 청산·확신도 모두 있는 건 {len(data)}건 "
                 f"(최소 {MIN_TOTAL}건 필요). 숫자를 내면 오해를 부릅니다.")
        return L

    L.append(f"  대상 {len(data)}건")
    rates: list[tuple[str, float]] = []
    for lo, hi in CONVICTION_BINS:
        sel = [_f(r.get("ret_pct")) for c, r in data if lo <= c < hi]
        sel = [x for x in sel if x is not None]
        label = f"{lo:.1f}~{hi if hi <= 1 else 1.0:.1f}"
        if len(sel) < MIN_BIN:
            L.append(f"  conviction {label}  n={len(sel)} (표본 부족, 생략)")
            continue
        L.append(f"  conviction {label}  {_stat_line(sel)}")
        rates.append((label, sum(1 for x in sel if x > 0) / len(sel)))

    # 순위상관 — 구간을 안 나누고 전체에서 본다.
    # 고정 임계값(예: |ρ|<0.1)으로 판정하면 안 된다. ρ의 귀무분포 폭은 표본 수에
    # 따라 달라서(σ≈1/√(n-1)), n=30이면 ±0.36까지는 순수 노이즈다. 고정 임계값은
    # 그 구간의 잡음을 "변별력 있음"이라는 녹색 신호로 바꿔버린다.
    if len(data) >= MIN_CORR:
        xs = [c for c, _ in data]
        ys = [_f(r.get("ret_pct")) for _, r in data]
        rho = spearman(xs, ys)
        if rho is not None:
            crit = critical_rho(len(data))
            L.append(f"  순위상관 ρ = {rho:+.3f}   "
                     f"(n={len(data)} 에서 우연히 나올 수 있는 범위 ±{crit:.3f})")
            if abs(rho) <= crit:
                L.append("  ⚠️ 우연 범위 안 — 확신도와 실현손익의 관계를 "
                         "현재 표본으로는 **구분할 수 없습니다.** 확신도를 추천 선별이나 "
                         "비중 조절에 쓸 근거가 아직 없습니다.")
            elif rho > 0:
                L.append("  ✅ 우연 범위를 넘는 양의 관계 — 확신도가 높을수록 성과가 "
                         "좋습니다. 비중 조절에 쓸 근거가 있습니다.")
            else:
                L.append("  🚨 우연 범위를 넘는 **음의** 관계 — 확신도가 높을수록 성과가 "
                         "나쁩니다. 프롬프트가 확신을 잘못된 신호에 걸고 있습니다.")
            # 95% 임계값은 20번에 1번 우연히 넘는다. 경계에 겨우 걸친 값을
            # 확정된 관계로 읽으면 그게 과거 XGBoost 실패의 경로다.
            if crit < abs(rho) <= crit * 1.3:
                L.append("  ※ 다만 경계에 겨우 걸친 값입니다(임계의 1.3배 이내). "
                         "95% 기준은 20번에 1번 우연히 넘습니다 — 표본이 더 쌓여도 "
                         "같은 방향이 유지되는지 확인한 뒤에 쓰세요.")
    else:
        L.append(f"  (순위상관은 {MIN_CORR}건부터 — 현재 {len(data)}건)")

    if len(rates) >= 2:
        inc = all(rates[i][1] <= rates[i + 1][1] for i in range(len(rates) - 1))
        L.append("  ✅ 구간별 승률이 단조 증가" if inc
                 else "  ⚠️ 구간별 승률이 단조 증가하지 않음")
    return L


# ──────────────────────────────────────────────────────────────────────────
# 2) LLM 자기 예측 캘리브레이션
# ──────────────────────────────────────────────────────────────────────────
def forecast_section(rows: list[dict]) -> list[str]:
    L = ["", "━━ 2. LLM 예측(P10/P50/P90)이 맞는가 ━━"]
    data = [r for r in rows if _f(r.get("p50_pct")) is not None]
    if len(data) < MIN_TOTAL:
        L.append(f"  표본 부족 — 예측·결과 모두 있는 건 {len(data)}건 "
                 f"(최소 {MIN_TOTAL}건 필요)")
        return L

    errs, acts, preds = [], [], []
    inside = 0
    covered = 0
    for r in data:
        act = _f(r.get("ret_pct"))
        p50 = _f(r.get("p50_pct"))
        errs.append(act - p50)
        acts.append(act)
        preds.append(p50)
        p10, p90 = _f(r.get("p10_pct")), _f(r.get("p90_pct"))
        if p10 is not None and p90 is not None:
            covered += 1
            if min(p10, p90) <= act <= max(p10, p90):
                inside += 1

    bias = sum(errs) / len(errs)
    mae = sum(abs(e) for e in errs) / len(errs)
    L.append(f"  대상 {len(data)}건")
    L.append(f"  편향(실제−예측 평균) {bias:+.2f}%p   "
             f"평균절대오차 {mae:.2f}%p   예측 중위 {_median(preds):+.2f}% / "
             f"실제 중위 {_median(acts):+.2f}%")
    if bias < -1.0:
        L.append("  ⚠️ LLM이 체계적으로 낙관적 — 예측이 실제보다 높습니다. "
                 "예상손익을 그대로 믿고 비중을 키우면 손실이 커집니다.")
    elif bias > 1.0:
        L.append("  ℹ️ LLM이 체계적으로 비관적 — 실제가 예측보다 좋습니다.")
    else:
        L.append("  ✅ 중심 예측의 편향은 작습니다(±1%p 내).")

    if covered >= MIN_TOTAL:
        rate = inside / covered * 100
        L.append(f"  P10~P90 구간 적중률 {rate:.1f}% ({inside}/{covered}) "
                 f"— 캘리브레이션이 맞다면 약 80%")
        if rate < 60:
            L.append("  ⚠️ 구간이 너무 좁습니다 — 실제 변동이 예측 범위를 자주 벗어납니다. "
                     "ATR 기반 범위 산출을 넓혀야 합니다.")
        elif rate > 95:
            L.append("  ℹ️ 구간이 너무 넓습니다 — 거의 다 들어맞지만 정보가 없습니다.")
    return L


# ──────────────────────────────────────────────────────────────────────────
# 3) 청산이 너무 이른가 — D+2 +27% 괴리의 후보 원인
# ──────────────────────────────────────────────────────────────────────────
def exit_timing_section(rows: list[dict]) -> list[str]:
    L = ["", "━━ 3. 청산이 너무 이른가 (실현 vs 최대 유리폭 MFE) ━━"]
    data = [r for r in rows if _f(r.get("mfe_pct")) is not None]
    if len(data) < MIN_BIN:
        L.append(f"  표본 부족 — MFE가 있는 청산 건 {len(data)}건")
        return L

    L.append(f"  대상 {len(data)}건")
    L.append(f"  {'보유기간':<8}{'n':>4}  {'실현중위':>8}{'MFE중위':>8}"
             f"{'회수율':>7}  {'평균보유':>8}")
    for hz in HORIZONS + ("(전체)",):
        sel = data if hz == "(전체)" else [r for r in data
                                           if str(r.get("horizon", "")) == hz]
        if len(sel) < MIN_BIN and hz != "(전체)":
            continue
        rets = [_f(r.get("ret_pct")) for r in sel]
        rets = [x for x in rets if x is not None]
        mfes = [_f(r.get("mfe_pct")) for r in sel]
        mfes = [x for x in mfes if x is not None]
        days = [_f(r.get("days_held")) for r in sel]
        days = [x for x in days if x is not None]
        mr, mm = _median(rets), _median(mfes)
        cap = (mr / mm * 100) if (mr is not None and mm and mm > 0) else None
        L.append(f"  {hz:<8}{len(sel):>4}  {mr:>+7.2f}% {mm:>+7.2f}% "
                 f"{('-' if cap is None else f'{cap:5.0f}%'):>7}  "
                 f"{(_median(days) or 0):>7.1f}일")

    all_r = [_f(r.get("ret_pct")) for r in data]
    all_m = [_f(r.get("mfe_pct")) for r in data]
    mr, mm = _median([x for x in all_r if x is not None]), \
             _median([x for x in all_m if x is not None])
    if mr is not None and mm and mm > 0:
        cap = mr / mm
        if cap < 0.4:
            L.append(f"  ⚠️ 유리하게 움직인 폭의 {cap * 100:.0f}% 만 가져갔습니다. "
                     "청산이 이르거나 목표가가 낮습니다 — 'D+2 단순보유가 더 좋았다'와 "
                     "일치하는 패턴입니다.")
        elif cap < 0.7:
            L.append(f"  ℹ️ 회수율 {cap * 100:.0f}% — 개선 여지가 있습니다.")
        else:
            L.append(f"  ✅ 회수율 {cap * 100:.0f}% — 청산 시점은 무리가 없습니다.")

    # 타임아웃 비중 — 목표·손절 어느 쪽도 못 닿고 시간만 소진한 비율
    kinds: dict[str, int] = {}
    for r in data:
        k = str(r.get("exit_type", "?"))
        kinds[k] = kinds.get(k, 0) + 1
    tot = sum(kinds.values())
    L.append("  청산 유형: " + " · ".join(
        f"{k} {v}건({v / tot * 100:.0f}%)" for k, v in sorted(kinds.items())))
    return L


# ──────────────────────────────────────────────────────────────────────────
# 4) LLM 손절·목표 vs 규칙엔진 — 컬럼이 쌓인 뒤부터
# ──────────────────────────────────────────────────────────────────────────
def llm_vs_rule_section(rows: list[dict]) -> list[str]:
    L = ["", "━━ 4. LLM이 덮어쓴 손절·목표 vs 규칙엔진 ━━"]
    data = [r for r in rows if _f(r.get("stop_rule")) is not None
            or _f(r.get("target_rule")) is not None]
    if not data:
        L.append("  아직 비교할 수 없습니다 — signals.csv 에는 최종 손절·목표만 "
                 "남고 규칙엔진 원본이 덮여 사라졌습니다.")
        L.append("  stop_rule/target_rule 컬럼을 추가했으니 다음 배치부터 쌓입니다. "
                 f"{MIN_TOTAL}건 모이면 이 섹션이 자동으로 채워집니다.")
        return L

    diff_stop = [(_f(r.get("stop")) - _f(r.get("stop_rule"))) / _f(r.get("close")) * 100
                 for r in data
                 if _f(r.get("stop")) is not None and _f(r.get("stop_rule"))
                 and _f(r.get("close"))]
    diff_tgt = [(_f(r.get("target")) - _f(r.get("target_rule"))) / _f(r.get("close")) * 100
                for r in data
                if _f(r.get("target")) is not None and _f(r.get("target_rule"))
                and _f(r.get("close"))]
    L.append(f"  대상 {len(data)}건")
    if diff_stop:
        L.append(f"  손절: LLM이 규칙보다 평균 {sum(diff_stop) / len(diff_stop):+.2f}%p "
                 f"(종가 대비). + 면 손절을 올려 더 타이트하게 잡았다는 뜻")
    if diff_tgt:
        L.append(f"  목표: LLM이 규칙보다 평균 {sum(diff_tgt) / len(diff_tgt):+.2f}%p")

    # 바뀐 쪽과 안 바뀐 쪽의 성과 비교
    changed = [r for r in data
               if _f(r.get("stop")) is not None and _f(r.get("stop_rule")) is not None
               and abs(_f(r.get("stop")) - _f(r.get("stop_rule"))) > 1e-9]
    kept = [r for r in data if r not in changed]
    for name, sel in (("LLM이 바꾼 건", changed), ("규칙 그대로", kept)):
        rets = [_f(r.get("ret_pct")) for r in sel]
        rets = [x for x in rets if x is not None]
        if len(rets) >= MIN_BIN:
            L.append(f"  {name:<12} {_stat_line(rets)}")
        else:
            L.append(f"  {name:<12} n={len(rets)} (표본 부족)")
    return L


# ──────────────────────────────────────────────────────────────────────────
def build_llm_report(rows: list[dict],
                     title: str = "LLM 계층 평가") -> str:
    data = _outcome_rows(rows)
    L = ["=" * 66, f"  {title}", "=" * 66,
         f"전체 기록 {len(rows)}건 중 평가 대상(청산 완료 + LLM 추천) {len(data)}건"]
    if not data:
        L += ["", "평가할 수 있는 건이 없습니다. 필요한 조건:",
              "  · exit_type 이 채워짐(라벨링 완료)",
              "  · source=batch 이고 recommend 가 있음(LLM을 거친 건)",
              "  · ret_pct 가 있음",
              "", "라벨링: .venv/bin/python -m stock_auto.tracking.run_labeler"]
        return "\n".join(L)

    L += conviction_section(data)
    L += forecast_section(data)
    L += exit_timing_section(data)
    L += llm_vs_rule_section(data)
    L += ["", "=" * 66,
          "※ 이 리포트는 'LLM 계층이 가치를 더하는가'만 봅니다. 규칙 시그널 자체의",
          "   품질은 신호 성과 리포트(run_report)를 보세요.",
          "※ 표본이 기준 미달인 항목은 숫자를 내지 않습니다 — 작은 표본의 상관을",
          "   근거로 삼는 것이 과거 XGBoost 실패의 경로였습니다(교훈 1번)."]
    return "\n".join(L)


def summary_verdict(rows: list[dict]) -> str:
    """한 줄 판정 — 일일 다이제스트에 끼우기 위한 짧은 요약."""
    data = _outcome_rows(rows)
    if len(data) < MIN_TOTAL:
        return (f"LLM 평가: 표본 {len(data)}/{MIN_TOTAL}건 — 아직 판정 불가")
    rets = [_f(r.get("ret_pct")) for r in data]
    rets = [x for x in rets if x is not None]
    conv = [(c, _f(r.get("ret_pct"))) for r in data
            if (c := _f(r.get("conviction"))) is not None]
    rho = (spearman([c for c, _ in conv], [y for _, y in conv])
           if len(conv) >= MIN_CORR else None)
    pf = _pf(rets)
    parts = [f"n={len(rets)}", f"PF {'-' if pf is None else f'{pf:.2f}'}"]
    if rho is not None:
        parts.append(f"확신도ρ {rho:+.2f}")
    return "LLM 평가: " + " · ".join(parts)
