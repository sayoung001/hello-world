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
        return None if v is None or v == "" else float(v)
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
        L.append(f"| L{reg} | {ss['n']} | {ss['win_rate']:.0f}% | "
                 f"{ss['avg_ret']:+.2f}% | {_fmt_pf(ss['pf'])} |")
    L += [""]

    # ── 실행 여부 ──
    exec_rows = [r for r in labeled if r.get("executed")]
    if exec_rows:
        L += ["## 5. 실행 여부별 (선택 편향 점검)", "",
              "| 실행 | 건수 | 승률 | 평균수익 |", "|---|---:|---:|---:|"]
        for val in ("yes", "no"):
            ss = _stats([r for r in exec_rows if r.get("executed") == val])
            if ss["n"]:
                L.append(f"| {val} | {ss['n']} | {ss['win_rate']:.0f}% | "
                         f"{ss['avg_ret']:+.2f}% |")
        L += ["",
              "> 실행한 것과 안 한 것의 성과가 크게 다르면 "
              "사람의 판단이 실제로 알파를 더하고 있다는 뜻입니다(또는 그 반대).", ""]

    # ── 다음 행동 ──
    L += ["## 6. 다음 행동", ""]
    if len(labeled) < 200:
        L += [f"- 표본 {len(labeled)}건 — 캘리브레이션 학습에는 **200건 이상** 권장. "
              f"수집 계속.", ]
    else:
        L += ["- 표본 200건 이상 확보 — **로지스틱 캘리브레이션 학습 가능**. "
              "`effective_score → p_win` 적합을 시작하세요."]
    L += ["- 구간별 승률이 단조 증가하지 않으면 점수 가중치 재검토 필요",
          "- 폭주 알림 승률이 배치 추천보다 현저히 낮으면 임계값 상향 검토", ""]
    return "\n".join(L)


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
             "rsi_14", "adx", "rvol", "mom_rank", "rr_ratio"]
    out = []
    for r in rows:
        if r.get("exit_type") not in ("tp", "sl"):
            continue
        rec = {k: _f(r.get(k)) for k in feats}
        rec["y"] = 1 if r["exit_type"] == "tp" else 0
        rec["symbol"] = r.get("symbol")
        rec["date"] = r.get("date")
        out.append(rec)
    return out
