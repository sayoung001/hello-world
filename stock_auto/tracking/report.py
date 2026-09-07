"""
성과 · 캘리브레이션 리포트 — 라벨링된 신호에서 보정 근거를 뽑아 출력한다.

핵심 산출물은 '캘리브레이션 표'다.
  Effective_Score 구간별 실제 승률 → 점수가 확률로 얼마나 변환되는지 보여준다.
  이 표가 §3.1 로지스틱 캘리브레이션 층의 입력이자 검증 수단이다.

출력: Markdown 문자열 (콘솔 · 파일 · Notion · Telegram 어디로든 보낼 수 있음)
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

# 캘리브레이션 구간 (Effective_Score)
SCORE_BINS = [(-99, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 5.0), (5.0, 6.0), (6.0, 99)]
MOM_BINS = [(0.0, 0.25), (0.25, 0.50), (0.50, 0.75), (0.75, 1.01)]


def _f(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return None if f != f else f     # NaN도 '값 없음'
    except (TypeError, ValueError):
        return None


def _stats(rows: list[dict]) -> dict:
    """승률·손익비·PF 등 기본 통계."""
    labeled = [r for r in rows if r.get("exit_type") in ("tp", "sl", "timeout")]
    n = len(labeled)
    if n == 0:
        return {"n": 0}
    rets = [_f(r.get("ret_pct")) or 0.0 for r in labeled]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    maes = [_f(r.get("mae_pct")) or 0.0 for r in labeled]
    mfes = [_f(r.get("mfe_pct")) or 0.0 for r in labeled]
    dirc = [int(r["direction_correct"]) for r in labeled
            if str(r.get("direction_correct", "")).strip() not in ("", "None")]
    return {
        "n": n,
        "win_rate": len(wins) / n * 100,
        "avg_ret": sum(rets) / n,
        "avg_win": (gross_win / len(wins)) if wins else 0.0,
        "avg_loss": (-gross_loss / len(losses)) if losses else 0.0,
        "pf": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "avg_mae": sum(maes) / n,
        "avg_mfe": sum(mfes) / n,
        "dir_acc": (sum(dirc) / len(dirc) * 100) if dirc else None,
        "tp": sum(1 for r in labeled if r["exit_type"] == "tp"),
        "sl": sum(1 for r in labeled if r["exit_type"] == "sl"),
        "to": sum(1 for r in labeled if r["exit_type"] == "timeout"),
    }


def _fmt_pf(pf: float) -> str:
    return "∞" if pf == float("inf") else f"{pf:.2f}"


def _bin_table(rows: list[dict], key: str, bins: list[tuple[float, float]],
               label: str) -> list[str]:
    """구간별 승률 표 — 캘리브레이션의 시각적 근거."""
    out = [f"### {label}", "",
           f"| 구간 | 건수 | 승률 | 평균수익 | PF | TP/SL/TO |",
           "|---|---:|---:|---:|---:|---|"]
    any_row = False
    for lo, hi in bins:
        sub = [r for r in rows
               if (_f(r.get(key)) is not None and lo <= _f(r.get(key)) < hi)]
        s = _stats(sub)
        if s["n"] == 0:
            continue
        any_row = True
        rng = f"{lo:g} ~ {hi:g}" if hi < 90 else f"{lo:g}+"
        out.append(f"| {rng} | {s['n']} | {s['win_rate']:.0f}% | "
                   f"{s['avg_ret']:+.2f}% | {_fmt_pf(s['pf'])} | "
                   f"{s['tp']}/{s['sl']}/{s['to']} |")
    if not any_row:
        out.append("| _데이터 없음_ | | | | | |")
    out.append("")
    return out


def build_report(rows: list[dict], title: str = "신호 성과 리포트") -> str:
    """라벨링된 기록 → Markdown 리포트."""
    total = len(rows)
    labeled = [r for r in rows if r.get("exit_type") in ("tp", "sl", "timeout")]
    skipped = [r for r in rows if r.get("exit_type") == "skip"]
    waiting = total - len(labeled) - len(skipped)
    L: list[str] = [f"# {title}", ""]

    # ── 요약 ──
    s = _stats(rows)
    L += ["## 1. 요약", "",
          f"- 총 신호 **{total}건** · 라벨 완료 **{len(labeled)}건** · "
          f"결과 대기 {waiting}건 · 미체결(갭) {len(skipped)}건"]
    if skipped:
        L += [f"- 미체결 {len(skipped)}건은 진입가가 이미 손절/목표를 넘어선 갭 케이스로 "
              f"성과 집계에서 제외됩니다"]
    if s["n"] == 0:
        L += ["", "> 아직 라벨링된 신호가 없습니다. "
              "`python -m stock_auto.pipeline.label_outcomes` 실행 후 다시 확인하세요.", ""]
        return "\n".join(L)

    L += [f"- 승률 **{s['win_rate']:.1f}%** · 평균수익 **{s['avg_ret']:+.2f}%** · "
          f"PF **{_fmt_pf(s['pf'])}**",
          f"- 평균 익절 {s['avg_win']:+.2f}% / 평균 손절 {s['avg_loss']:+.2f}%",
          f"- 평균 MFE(순행) {s['avg_mfe']:+.2f}% / 평균 MAE(역행) {s['avg_mae']:+.2f}%",
          f"- 청산 유형 — TP {s['tp']} · SL {s['sl']} · 타임아웃 {s['to']}"]
    if s["dir_acc"] is not None:
        L += [f"- 방향 정확도 **{s['dir_acc']:.1f}%** "
              f"(MFE ≥ |MAE| 기준)"]
    L += [""]

    # 진단 힌트 — 메타라벨링 필요 신호
    if s["sl"] > 0 and s["dir_acc"] is not None and s["dir_acc"] > 60 \
            and s["sl"] / max(s["n"], 1) > 0.35:
        L += ["> **진단** — 방향 정확도는 높은데 SL 히트 비중이 큽니다. "
              "신호가 아니라 *실행 시점/손절 폭*의 문제일 가능성이 큽니다 → "
              "메타라벨링 또는 동적 SL 검토.", ""]

    # ── 캘리브레이션 ──
    L += ["## 2. 캘리브레이션", "",
          "점수가 실제 승률로 얼마나 잘 변환되는지. "
          "구간이 올라갈수록 승률이 단조 증가해야 정상입니다.", ""]
    L += _bin_table(labeled, "effective_score", SCORE_BINS,
                    "Effective Score 구간별")
    L += _bin_table(labeled, "mom_rank", MOM_BINS,
                    "횡단면 모멘텀 순위 구간별")

    # 단조성 점검
    mono = _monotonic_check(labeled, "effective_score", SCORE_BINS)
    if mono is not None:
        L += [f"> **단조성 점검** — {mono}", ""]

    # ── 소스별 ──
    L += ["## 3. 소스별 (배치 추천 vs 폭주 알림)", "",
          "| 소스 | 건수 | 승률 | 평균수익 | PF |", "|---|---:|---:|---:|---:|"]
    for src, name in (("batch", "배치 추천"), ("alert", "폭주 알림")):
        sub = [r for r in labeled if r.get("source") == src]
        ss = _stats(sub)
        if ss["n"] == 0:
            L.append(f"| {name} | 0 | – | – | – |")
        else:
            L.append(f"| {name} | {ss['n']} | {ss['win_rate']:.0f}% | "
                     f"{ss['avg_ret']:+.2f}% | {_fmt_pf(ss['pf'])} |")
    L += ["",
          "> 폭주 알림의 승률이 곧 **알림 정밀도**입니다. "
          "재현율보다 이 수치를 우선 관리하세요.", ""]

    # ── 레짐별 ──
    L += ["## 4. 매크로 레짐별", "",
          "| 레짐 | 건수 | 승률 | 평균수익 | PF |", "|---|---:|---:|---:|---:|"]
    by_reg: dict[str, list] = defaultdict(list)
    for r in labeled:
        by_reg[str(r.get("macro_regime", "?"))].append(r)
    for reg in sorted(by_reg):
        ss = _stats(by_reg[reg])
        # 알림 기록에는 매크로 레짐이 없다 — 'L' 같은 빈 라벨 대신 '미상'으로 표기
        name = f"L{reg}" if str(reg).strip() not in ("", "?", "None") else "미상"
        L.append(f"| {name} | {ss['n']} | {ss['win_rate']:.0f}% | "
                 f"{ss['avg_ret']:+.2f}% | {_fmt_pf(ss['pf'])} |")
    L += [""]

    # ── 게이트 실효성 ──
    L += _gate_section(labeled)

    # ── 실행 여부 ──
    L += _triage_section(labeled, rows)

    # ── 다음 행동 ──
    L += ["## 7. 다음 행동", ""]
    n_untriaged = sum(1 for r in rows
                      if r.get("source") == "batch" and not r.get("gated_by")
                      and not r.get("executed"))
    if n_untriaged:
        L += [f"- **실행 여부 미입력 {n_untriaged}건** — "
              "`python -m stock_auto.pipeline.triage` (소급 입력 불가, 매일 처리 권장)"]
    if len(labeled) < 200:
        L += [f"- 표본 {len(labeled)}건 — 캘리브레이션 학습에는 **200건 이상** 권장. "
              f"수집 계속.", ]
    else:
        L += ["- 표본 200건 이상 확보 — **로지스틱 캘리브레이션 학습 가능**. "
              "`effective_score → p_win` 적합을 시작하세요."]
    L += ["- 구간별 승률이 단조 증가하지 않으면 점수 가중치 재검토 필요",
          "- 폭주 알림 승률이 배치 추천보다 현저히 낮으면 임계값 상향 검토", ""]
    return "\n".join(L)


def _gate_section(labeled: list[dict]) -> list[str]:
    """
    게이트(매크로/섹터/실적)가 실제로 손실을 막았는지.

    차단분도 기록하기 때문에 비로소 가능한 비교다. 차단분의 성과가 통과분보다
    좋다면 그 게이트는 수익 기회를 버리고 있다는 뜻이다.
    """
    passed = [r for r in labeled if not r.get("gated_by")]
    blocked = [r for r in labeled if r.get("gated_by")]
    L = ["## 5. 게이트 실효성 (통과분 vs 차단분)", ""]
    if not blocked:
        L += ["> 차단된 신호가 아직 라벨링되지 않았습니다. "
              "게이트가 한 번도 발동하지 않았거나(상승장) 기록 기간이 짧습니다.", ""]
        return L
    L += ["| 구분 | 건수 | 승률 | 평균수익 | PF |", "|---|---:|---:|---:|---:|"]
    for name, sub in (("게이트 통과(실제 추천)", passed), ("게이트 차단", blocked)):
        s = _stats(sub)
        if s["n"] == 0:
            L.append(f"| {name} | 0 | – | – | – |")
        else:
            L.append(f"| {name} | {s['n']} | {s['win_rate']:.0f}% | "
                     f"{s['avg_ret']:+.2f}% | {_fmt_pf(s['pf'])} |")
    # 사유별
    by_reason: dict[str, list] = defaultdict(list)
    for r in blocked:
        for reason in str(r.get("gated_by", "")).split(","):
            if reason:
                by_reason[reason].append(r)
    if by_reason:
        L += ["", "### 차단 사유별", "",
              "| 사유 | 건수 | 승률 | 평균수익 |", "|---|---:|---:|---:|"]
        label = {"macro": "매크로 레짐", "sector": "섹터 하락위험",
                 "earnings": "실적 발표 임박"}
        for reason in sorted(by_reason):
            s = _stats(by_reason[reason])
            if s["n"]:
                L.append(f"| {label.get(reason, reason)} | {s['n']} | "
                         f"{s['win_rate']:.0f}% | {s['avg_ret']:+.2f}% |")
    ps, bs = _stats(passed), _stats(blocked)
    if ps["n"] >= 20 and bs["n"] >= 20:
        if bs["avg_ret"] > ps["avg_ret"]:
            L += ["", "> ⚠️ **차단분의 성과가 통과분보다 좋습니다.** "
                  "게이트가 수익 기회를 버리고 있을 수 있습니다 — 임계 완화를 검토하세요.", ""]
        else:
            L += ["", f"> 게이트가 평균 {ps['avg_ret'] - bs['avg_ret']:+.2f}%p 만큼 "
                  "성과를 지키고 있습니다.", ""]
    else:
        L += ["", "> 양쪽 표본이 20건 이상 쌓여야 판단할 수 있습니다.", ""]
    return L


def _triage_section(labeled: list[dict], all_rows: list[dict]) -> list[str]:
    """사람의 실행 판단이 알파를 더하는가 — 반자동 시스템의 핵심 지표."""
    L = ["## 6. 실행 여부별 (선택 편향 점검)", ""]
    exec_rows = [r for r in labeled if r.get("executed") in ("yes", "no")]
    untriaged = sum(1 for r in all_rows
                    if r.get("source") == "batch" and not r.get("gated_by")
                    and not r.get("executed"))
    if not exec_rows:
        L += [f"> 실행 여부가 입력된 기록이 없습니다 (미입력 {untriaged}건). ",
              "> `python -m stock_auto.pipeline.triage` 로 입력하세요. "
              "**소급 입력이 불가하므로 매일 처리해야 합니다.**", ""]
        return L
    L += ["| 실행 | 건수 | 승률 | 평균수익 | PF |", "|---|---:|---:|---:|---:|"]
    for val, name in (("yes", "샀다"), ("no", "안 샀다")):
        s = _stats([r for r in exec_rows if r.get("executed") == val])
        if s["n"]:
            L.append(f"| {name} | {s['n']} | {s['win_rate']:.0f}% | "
                     f"{s['avg_ret']:+.2f}% | {_fmt_pf(s['pf'])} |")
    ys = _stats([r for r in exec_rows if r.get("executed") == "yes"])
    ns = _stats([r for r in exec_rows if r.get("executed") == "no"])
    L += [""]
    if ys["n"] >= 20 and ns["n"] >= 20:
        d = ys["avg_ret"] - ns["avg_ret"]
        verdict = ("사람의 선별이 알파를 더하고 있습니다" if d > 0 else
                   "사람의 선별이 오히려 성과를 깎고 있습니다 — 규칙대로 전 건 실행을 검토하세요")
        L += [f"> 산 것이 안 산 것보다 평균 **{d:+.2f}%p**. {verdict}.", ""]
    else:
        L += ["> 양쪽 20건 이상 쌓이면 사람의 개입 가치가 판정됩니다 "
              f"(현재 샀다 {ys['n']} / 안 샀다 {ns['n']}).", ""]
    if untriaged:
        L += [f"> 미입력 {untriaged}건이 남아 있습니다.", ""]
    return L


def _monotonic_check(rows: list[dict], key: str,
                     bins: list[tuple[float, float]]) -> Optional[str]:
    rates = []
    for lo, hi in bins:
        sub = [r for r in rows
               if (_f(r.get(key)) is not None and lo <= _f(r.get(key)) < hi)]
        s = _stats(sub)
        if s["n"] >= 5:
            rates.append((lo, s["win_rate"]))
    if len(rates) < 2:
        return None
    inc = all(rates[i][1] <= rates[i + 1][1] for i in range(len(rates) - 1))
    if inc:
        return "✅ 승률이 점수 구간에 따라 단조 증가 — 점수 체계가 변별력을 가짐"
    return ("⚠️ 승률이 단조 증가하지 않음 — 점수 가중치가 실제 확률을 "
            "제대로 반영하지 못할 수 있음")


def calibration_dataset(rows: list[dict]) -> list[dict]:
    """
    로지스틱 캘리브레이션 학습용 데이터셋 추출.
    타깃 y: TP=1, SL=0 (타임아웃은 제외 — 이진 분류를 흐리지 않도록)
    """
    feats = ["effective_score", "money_score", "price_score", "liquidity_score",
             "penalty", "stock_regime", "macro_regime", "sector_status_score",
             "rsi_14", "adx", "rvol", "mom_rank", "rr_ratio", "days_to_earnings"]
    out = []
    for r in rows:
        if r.get("exit_type") not in ("tp", "sl"):
            continue
        rec = {k: _f(r.get(k)) for k in feats}
        rec["y"] = 1 if r["exit_type"] == "tp" else 0
        rec["symbol"] = r.get("symbol")
        rec["date"] = r.get("date")
        # 게이트 차단분도 학습에 넣는다 — 게이트는 '실행 결정'이지 '신호 품질'이 아니다.
        # 차단분을 빼면 하락 레짐 구간이 통째로 빠져 점수→승률 곡선이 상승장에 과적합된다.
        rec["gated_by"] = r.get("gated_by", "")
        out.append(rec)
    return out
