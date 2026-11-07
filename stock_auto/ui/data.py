"""
UI 스냅샷 — 로컬 파일(signals.csv·triggers.csv·usage.csv·logs/ui·daemon.log)만 읽어 JSON 하나로.

네트워크·LLM·노션을 전혀 건드리지 않는다. 그래서 배치가 돌고 있어도, 외부가 죄다
막혀 있어도 화면은 뜬다. 모든 함수는 실패하면 빈 값으로 돌아간다(화면이 죽지 않게).
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from stock_auto.ui import status as ST

KST = ZoneInfo("Asia/Seoul")


def _f(v) -> Optional[float]:
    try:
        if v in (None, ""):
            return None
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _since(days: int) -> str:
    return (datetime.now(KST) - timedelta(days=days)).strftime("%Y-%m-%d")


# ── 추천 ──────────────────────────────────────────────────────────────────
def recommendations(days: int = 10) -> list[dict]:
    """최근 days 일의 배치 신호(추천 + 게이트 차단). 국내장은 회사명·관련 ETF 를 붙인다."""
    try:
        from stock_auto.tracking import store, pnl
        rows = store.load()
    except Exception:  # noqa: BLE001
        return []
    cut = _since(days)
    sel = [r for r in rows if r.get("source") == "batch" and str(r.get("date", "")) >= cut]
    kr_codes = [str(r["symbol"]) for r in sel if str(r.get("market")) == "KR"]
    names: dict[str, str] = {}
    etfs: dict[str, list] = {}
    if kr_codes:
        try:
            from stock_auto.markets.kr import universe as kru, etf as kre
            names = kru.load_names(kr_codes)
            etfs = kre.bundle(kr_codes, names=names)
        except Exception:  # noqa: BLE001
            pass
    out = []
    for r in sel:
        sym = str(r.get("symbol", ""))
        mk = str(r.get("market") or "US")
        out.append({
            "signal_id": r.get("signal_id", ""), "date": r.get("date", ""), "market": mk,
            "symbol": sym, "name": names.get(sym, "") if mk == "KR" else "",
            "recommend": r.get("recommend", ""), "horizon": r.get("horizon", ""),
            "conviction": _f(r.get("conviction")), "effective": _f(r.get("effective_score")),
            "close": _f(r.get("close")), "stop": _f(r.get("stop")), "target": _f(r.get("target")),
            "rr": _f(r.get("rr_ratio")), "entry_rule": r.get("entry_rule", ""),
            "p10": _f(r.get("p10_pct")), "p50": _f(r.get("p50_pct")), "p90": _f(r.get("p90_pct")),
            "gated_by": r.get("gated_by", ""), "executed": r.get("executed", ""),
            "exit_type": r.get("exit_type", ""), "ret_pct": _f(r.get("ret_pct")),
            "days_held": _f(r.get("days_held")), "news_count": _f(r.get("news_count")),
            "is_reco": pnl.is_recommendation(r),
            "etfs": [{"code": e["code"], "name": e["name"], "kind": e["kind"]}
                     for e in etfs.get(sym, [])] if mk == "KR" else [],
        })
    out.sort(key=lambda x: (x["date"], -(x["effective"] or 0)), reverse=True)
    return out


# ── 트리거 ────────────────────────────────────────────────────────────────
def triggers(days: int = 21) -> list[dict]:
    try:
        from stock_auto.tracking import triggers as T
        rows = T.load()
    except Exception:  # noqa: BLE001
        return []
    cut = _since(days)
    out = []
    for r in rows:
        if str(r.get("date", "")) < cut:
            continue
        out.append({k: r.get(k, "") for k in (
            "trigger_id", "date", "market", "symbol", "kind", "value_pct", "basis",
            "basis_price", "target_price", "stop", "target", "horizon", "recommend",
            "status", "fired_at", "fired_price", "rule_text", "notion_url", "source", "split")})
    order = {"pending": 0, "fired": 1, "cancelled": 2, "expired": 3}
    out.sort(key=lambda x: (order.get(str(x["status"]), 9), x["date"]), reverse=False)
    out.sort(key=lambda x: order.get(str(x["status"]), 9))
    return out


# ── 성과 ──────────────────────────────────────────────────────────────────
def performance() -> dict:
    try:
        from stock_auto.tracking import store, pnl, forward
        rows = store.load()
        fwd = forward.load()
    except Exception:  # noqa: BLE001
        return {}
    try:
        s = pnl.summarize(pnl.closed_trades(rows))
        s.pop("curve", None)
        for k in ("best", "worst"):
            if isinstance(s.get(k), dict):
                s[k] = {"symbol": s[k].get("symbol"), "pnl_usd": s[k].get("pnl_usd")}
        by_grade = {}
        for g in pnl.RECOMMENDED:
            sg = pnl.summarize(pnl.closed_trades(rows, recommend=g))
            by_grade[g] = {k: sg.get(k) for k in ("n", "total_pnl", "win_rate", "pf", "avg_days")}
        se = pnl.summarize(pnl.closed_trades(rows, executed_only=True))
        cv = forward.curve(fwd)
        best = forward.best_horizon(cv)
        recos = [r for r in rows if pnl.is_recommendation(r)]
        return {
            "summary": s, "by_grade": by_grade,
            "executed": {k: se.get(k) for k in ("n", "total_pnl", "win_rate", "pf")},
            "curve": cv, "best": best,
            "total": len(recos),
            "waiting": sum(1 for r in recos if not r.get("exit_type") or r.get("exit_type") == "nodata"),
            "skipped": sum(1 for r in recos if r.get("exit_type") == "skip"),
            "untriaged": sum(1 for r in recos if not r.get("executed")),
            "telegram_text": pnl.telegram_summary(rows, fwd),
            "attribution_text": _attribution_text(rows),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _attribution_text(rows: list[dict]) -> str:
    """수익 vs 손실 공통점 — 로컬 CSV 만 읽으므로 스냅샷마다 새로 계산해도 가볍다."""
    try:
        from stock_auto.tracking import attribution
        return attribution.telegram_text(attribution.analyze(rows))
    except Exception as e:  # noqa: BLE001
        return f"분석 실패: {type(e).__name__}: {e}"


# ── 사용량 ────────────────────────────────────────────────────────────────
def usage(limit: int = 15) -> dict:
    try:
        from stock_auto.tracking import usage_log
        rows = usage_log.load()
    except Exception:  # noqa: BLE001
        return {"rows": [], "summary": {}}
    try:
        summ = usage_log.summary(rows)
    except Exception:  # noqa: BLE001
        summ = {}
    return {"rows": rows[-limit:][::-1], "summary": summ}


# ── 상태 ──────────────────────────────────────────────────────────────────
def autopull() -> dict:
    beat = ST.app_root() / "logs" / "autopull.heartbeat"
    state = ST.app_root() / "logs" / "autopull_alert.state"
    out: dict[str, Any] = {"heartbeat_age_min": None, "stuck": False, "alert_state": ""}
    try:
        if beat.exists():
            out["heartbeat_age_min"] = round((time.time() - beat.stat().st_mtime) / 60, 1)
            out["stuck"] = out["heartbeat_age_min"] > 30
        if state.exists():
            out["alert_state"] = state.read_text(encoding="utf-8", errors="ignore")[:200]
    except OSError:
        pass
    return out


def schedule() -> list[str]:
    try:
        from stock_auto.pipeline.run_daemon import schedule_lines
        return schedule_lines()
    except Exception as e:  # noqa: BLE001
        return [f"시간표 조회 실패: {type(e).__name__}: {e}"]


def git_head() -> str:
    try:
        import subprocess
        r = subprocess.run(["git", "-C", str(ST.app_root()), "log", "-1", "--format=%h %cd %s", "--date=short"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()[:120]
    except Exception:  # noqa: BLE001
        return ""


def log_tail(n: int = 200, name: str = "daemon.log") -> str:
    if "/" in name or ".." in name:
        return ""
    p = ST.app_root() / "logs" / name
    if not p.exists():
        return f"({p} 없음 — VM 에서 systemd 로 돌 때 생긴다)"
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 400_000))
            data = f.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        return "\n".join(lines[-n:])
    except OSError as e:
        return f"읽기 실패: {e}"


def snapshot(days: int = 10) -> dict:
    return {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
        "data_dir": os.environ.get("STOCK_DATA_DIR", "data"),
        "git": git_head(),
        "schedule": schedule(),
        "autopull": autopull(),
        "last_batches": ST.read_last_batches(),
        "failures": ST.read_failures(),
        "digests": ST.list_digests(),
        "recommendations": recommendations(days),
        "triggers": triggers(),
        "performance": performance(),
        "usage": usage(),
    }
