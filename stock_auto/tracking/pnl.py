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


RECOMMENDED = ("BUY", "WATCH")


def is_recommendation(r: dict) -> bool:
    """가상계좌·요약이 '추천'으로 세는 행.

    source=batch 이고 게이트에 안 막혔고 **추천이 BUY/WATCH 인 것**.
      · AVOID 는 "사지 마라"다 — 산 것으로 치면 안 된다.
      · recommend 가 빈 행은 후보였지만 LLM 분석이 실패한 건(10-20 스키마 400)이다.
        추천을 받은 적이 없는데 '추천 누적'과 '실행 여부 미입력'에 들어가 있었다.
    """
    return (r.get("source") == "batch" and not r.get("gated_by")
            and str(r.get("recommend") or "").upper() in RECOMMENDED)


def closed_trades(rows: list[dict], executed_only: bool = False,
                  recommend: Optional[str] = None) -> list[dict[str, Any]]:
    """손익 계산 대상 거래 목록(청산 완료분). recommend 를 주면 그 등급(BUY/WATCH)만."""
    size, fee = position_usd(), fee_bps()
    out = []
    for r in rows:
        if not is_recommendation(r):
            continue
        if recommend and str(r.get("recommend") or "").upper() != recommend:
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
    recos = [r for r in rows if is_recommendation(r)]
    s = summarize(closed_trades(rows))
    total = len(recos)
    waiting = sum(1 for r in recos if not r.get("exit_type") or r.get("exit_type") == "nodata")
    skipped = sum(1 for r in recos if r.get("exit_type") == "skip")
    n_buy = sum(1 for r in recos if str(r.get("recommend")).upper() == "BUY")
    n_watch = total - n_buy
    # 후보였지만 LLM 분석이 실패해 추천을 못 받은 행 — 추천이 아니므로 따로 센다
    n_nolabel = sum(1 for r in rows if r.get("source") == "batch" and not r.get("gated_by")
                    and not str(r.get("recommend") or "").strip())
    n_avoid = sum(1 for r in rows if r.get("source") == "batch" and not r.get("gated_by")
                  and str(r.get("recommend") or "").upper() == "AVOID")
    # 누적 = 청산 + 대기 + 미체결(갭) 이 맞아야 읽는 사람이 믿는다
    head = (f"추천 누적 {total}건(BUY {n_buy} · WATCH {n_watch}) · 청산 완료 {s['n']}건 · "
            f"결과 대기 {waiting}건" + (f" · 미체결(갭) {skipped}건" if skipped else ""))
    L = ["📊 추천 성과 주간 요약", head]
    if n_avoid or n_nolabel:
        L.append(f"   (집계 제외: AVOID {n_avoid}건 · LLM 미분석 후보 {n_nolabel}건)")
    if s["n"]:
        L += [f"가상계좌(${s['position']:,.0f}/종목): 총 손익 ${s['total_pnl']:+,.2f}",
              f"승률 {s['win_rate']:.0f}% · PF {_pf(s['pf'])} · 평균 보유 {s['avg_days']:.1f}일",
              f"TP {s['tp']} / SL {s['sl']} / 타임아웃 {s['to']} · 최대낙폭 ${s['max_dd']:,.0f}"]
        # BUY 와 WATCH 를 따로 — '확신 있는 추천'이 실제로 더 나은지가 핵심 질문이다
        parts = []
        for grade in RECOMMENDED:
            sg = summarize(closed_trades(rows, recommend=grade))
            if sg["n"]:
                parts.append(f"{grade} {sg['n']}건 ${sg['total_pnl']:+,.0f} 승률 {sg['win_rate']:.0f}% PF {_pf(sg['pf'])}")
        if len(parts) == 2:
            L.append(" · ".join(parts))
        se = summarize(closed_trades(rows, executed_only=True))
        if se["n"]:
            auto = any(str(r.get("triage_note", "")).startswith("자동") for r in recos
                       if r.get("executed") == "yes")
            tag = "실행 표시(자동 전량매수 가정)" if auto else "실제 실행"
            L.append(f"{tag} {se['n']}건: ${se['total_pnl']:+,.2f} · 승률 {se['win_rate']:.0f}%")
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
    # 미입력은 BUY/WATCH 만 — AVOID·LLM 미분석 행에 "샀나요?"를 묻는 건 소음이다
    n_untriaged = sum(1 for r in recos if not r.get("executed"))
    if n_untriaged:
        L.append(f"⚠ 실행 여부 미입력 {n_untriaged}건 → .venv/bin/python -m stock_auto.pipeline.triage")
    return "\n".join(L)
