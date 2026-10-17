"""
가상 계좌 — "추천을 전부 같은 금액으로 샀다면 돈이 얼마나 됐나".

규칙(단순·투명):
  - 종목당 고정 투입금 STOCK_TRACK_POSITION_USD (기본 1,000달러)
  - 진입/청산은 labeler 결과 그대로(다음날 시가 진입 → TP/SL/타임아웃 청산)
  - 수수료·슬리피지 없음(있으면 .env STOCK_TRACK_FEE_BPS, 왕복 bp)
  - 게이트 차단분·알림(alert)은 제외, 미체결(skip)은 제외
  - '실행 yes'만 따로 집계해 실제 매매와 비교

산출: 총 손익($), 승률, 손익비, 평균 보유일, 월별 손익, 청산일 기준 에쿼티 곡선
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Optional


def position_usd() -> float:
    try:
        return float(os.environ.get("STOCK_TRACK_POSITION_USD", "1000") or 1000)
    except ValueError:
        return 1000.0


def fee_bps() -> float:
    try:
        return float(os.environ.get("STOCK_TRACK_FEE_BPS", "0") or 0)
    except ValueError:
        return 0.0


def _f(v) -> Optional[float]:
    try:
        if v in (None, ""):
            return None
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def closed_trades(rows: list[dict], executed_only: bool = False) -> list[dict[str, Any]]:
    """손익 계산 대상 거래 목록(청산 완료분)."""
    size, fee = position_usd(), fee_bps()
    out = []
    for r in rows:
        if r.get("source") != "batch" or r.get("gated_by"):
            continue
        if r.get("exit_type") not in ("tp", "sl", "timeout"):
            continue
        if executed_only and r.get("executed") != "yes":
            continue
        ret = _f(r.get("ret_pct"))
        if ret is None:
            continue
        net = ret - fee / 100.0
        out.append({
            "signal_id": r.get("signal_id"), "date": r.get("date"),
            "symbol": r.get("symbol"), "recommend": r.get("recommend"),
            "horizon": r.get("horizon"), "exit_type": r.get("exit_type"),
            "exit_date": r.get("exit_date"), "days_held": int(_f(r.get("days_held")) or 0),
            "ret_pct": net, "pnl_usd": round(size * net / 100.0, 2),
            "executed": r.get("executed", ""),
        })
    out.sort(key=lambda t: (t["exit_date"] or "", t["date"] or ""))
    return out


def summarize(trades: list[dict]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "position": position_usd()}
    pnls = [t["pnl_usd"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gw, gl = sum(wins), abs(sum(losses))
    days = [t["days_held"] for t in trades]
    # 에쿼티 곡선(청산일 순 누적)과 최대 낙폭
    eq, peak, mdd, curve = 0.0, 0.0, 0.0, []
    for t in trades:
        eq += t["pnl_usd"]
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
        curve.append({"date": t["exit_date"], "equity": round(eq, 2), "symbol": t["symbol"]})
    monthly: dict[str, float] = defaultdict(float)
    for t in trades:
        monthly[(t["exit_date"] or "")[:7]] += t["pnl_usd"]
    return {
        "n": n, "position": position_usd(),
        "total_pnl": round(sum(pnls), 2),
        "total_ret_pct": round(sum(pnls) / position_usd() * 100 / n, 3),   # 거래당 평균 %
        "win_rate": len(wins) / n * 100,
        "avg_win": (gw / len(wins)) if wins else 0.0,
        "avg_loss": (-gl / len(losses)) if losses else 0.0,
        "pf": (gw / gl) if gl > 0 else float("inf"),
        "avg_days": sum(days) / n,
        "max_dd": round(mdd, 2),
        "best": max(trades, key=lambda t: t["pnl_usd"]),
        "worst": min(trades, key=lambda t: t["pnl_usd"]),
        "monthly": dict(sorted(monthly.items())),
        "curve": curve,
        "tp": sum(1 for t in trades if t["exit_type"] == "tp"),
        "sl": sum(1 for t in trades if t["exit_type"] == "sl"),
        "to": sum(1 for t in trades if t["exit_type"] == "timeout"),
    }


def _pf(v: float) -> str:
    return "∞" if v == float("inf") else f"{v:.2f}"


def section_md(rows: list[dict]) -> list[str]:
    """리포트 섹션 — 전체 추천 vs 실제 실행분."""
    L = [f"## 9. 가상 계좌 (종목당 ${position_usd():,.0f} 균등 투입 · 수수료 {fee_bps():g}bp)", ""]
    for title, ex in (("전체 추천", False), ("실제 실행(yes)만", True)):
        s = summarize(closed_trades(rows, executed_only=ex))
        if s["n"] == 0:
            L += [f"### {title}", "", "_청산 완료 거래 없음_", ""]
            continue
        L += [f"### {title} — {s['n']}건",
              "",
              f"- 총 손익 **${s['total_pnl']:+,.2f}** · 거래당 평균 {s['total_ret_pct']:+.2f}% · "
              f"최대 낙폭 ${s['max_dd']:,.2f}",
              f"- 승률 **{s['win_rate']:.1f}%** · 평균 익절 ${s['avg_win']:+,.2f} / "
              f"평균 손절 ${s['avg_loss']:+,.2f} · PF {_pf(s['pf'])}",
              f"- 평균 보유일 **{s['avg_days']:.1f}일** · TP {s['tp']} / SL {s['sl']} / "
              f"타임아웃 {s['to']}",
              f"- 최고 {s['best']['symbol']} ${s['best']['pnl_usd']:+,.2f} / "
              f"최악 {s['worst']['symbol']} ${s['worst']['pnl_usd']:+,.2f}", ""]
        if s["monthly"]:
            L += ["| 월 | 손익 |", "|---|---:|"]
            L += [f"| {m} | ${v:+,.2f} |" for m, v in s["monthly"].items()]
            L.append("")
    return L


def telegram_summary(rows: list[dict], fwd_rows: Optional[list[dict]] = None) -> str:
    """주간 텔레그램용 짧은 요약(평문)."""
    from stock_auto.tracking import forward
    s = summarize(closed_trades(rows))
    total = sum(1 for r in rows if r.get("source") == "batch" and not r.get("gated_by"))
    waiting = sum(1 for r in rows if r.get("source") == "batch" and not r.get("gated_by")
                  and not r.get("exit_type"))
    L = ["📊 추천 성과 주간 요약",
         f"추천 누적 {total}건 · 청산 완료 {s['n']}건 · 결과 대기 {waiting}건"]
    if s["n"]:
        L += [f"가상계좌(${s['position']:,.0f}/종목): 총 손익 ${s['total_pnl']:+,.2f}",
              f"승률 {s['win_rate']:.0f}% · PF {_pf(s['pf'])} · 평균 보유 {s['avg_days']:.1f}일",
              f"TP {s['tp']} / SL {s['sl']} / 타임아웃 {s['to']} · 최대낙폭 ${s['max_dd']:,.0f}"]
        se = summarize(closed_trades(rows, executed_only=True))
        if se["n"]:
            L.append(f"실제 실행 {se['n']}건: ${se['total_pnl']:+,.2f} · 승률 {se['win_rate']:.0f}%")
    else:
        L.append("아직 청산 완료 거래가 없습니다 (보유기간 경과 대기)")
    if fwd_rows:
        cv = forward.curve(fwd_rows)
        b = forward.best_horizon(cv)
        if b:
            L.append(f"보유일 곡선: D+{b['day']}가 최고 ({b['avg']:+.2f}%, {b['n']}건)")
        else:
            got = [c for c in cv if c.get("n")]
            if got:
                c = got[-1]
                L.append(f"보유일 곡선: D+{c['day']} 평균 {c['avg']:+.2f}% ({c['n']}건, 표본 부족)")
    n_untriaged = sum(1 for r in rows if r.get("source") == "batch"
                      and not r.get("gated_by") and not r.get("executed"))
    if n_untriaged:
        L.append(f"⚠ 실행 여부 미입력 {n_untriaged}건 → python -m stock_auto.pipeline.triage")
    return "\n".join(L)
