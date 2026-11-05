"""
운영 상태 기록 — 배치가 끝날 때·실패할 때 작은 JSON 과 다이제스트 원문을 남긴다.

왜 파일인가:
  UI 는 "마지막 배치가 언제 돌았고 무엇을 냈나"를 알아야 하는데, 그 사실은 지금
  텔레그램 메시지와 데몬 로그에만 있다. 배치가 끝나는 자리에서 한 줄 적어 두면
  UI 는 그것만 읽으면 된다(로그를 파싱하지 않는다).

위치: <앱 루트>/logs/ui/
  last_batch_US.json / last_batch_KR.json   마지막 배치 요약
  failures.jsonl                             배치 실패 기록(append)
  digest_<MKT>_<YYYY-MM-DD>.txt              텔레그램으로 간 다이제스트 원문
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def app_root() -> Path:
    return Path(__file__).resolve().parents[2]


def ui_dir() -> Path:
    """기본 <앱 루트>/logs/ui. 테스트·별도 배치에서는 STOCK_UI_LOG_DIR 로 바꿀 수 있다."""
    import os
    d = Path(os.environ.get("STOCK_UI_LOG_DIR", "").strip() or (app_root() / "logs" / "ui"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def record_batch(result: Any, digest_text: str = "", n_charts: int = 0) -> Optional[Path]:
    """BatchResult → last_batch_<MKT>.json + digest_<MKT>_<date>.txt. 실패해도 조용히."""
    try:
        market = str(getattr(result.market, "value", result.market))
        screen = result.screen
        recos = list(getattr(result.agents, "recommendations", []) or [])
        info = {
            "market": market,
            "date": result.date,
            "as_of": getattr(screen, "as_of", "") or result.date,
            "finished_at": _now(),
            "macro_level": getattr(screen.macro, "level", None),
            "macro_label": getattr(screen.macro, "label", ""),
            "macro_detail": getattr(screen.macro, "detail", ""),
            "n_scored": 0 if screen.scored_all is None else int(len(screen.scored_all)),
            "n_candidates": 0 if screen.candidates is None else int(len(screen.candidates)),
            "n_reco": len(recos),
            "n_fail": len(getattr(result.agents, "failures", []) or []),
            "n_gated": 0 if getattr(screen, "gated", None) is None else int(len(screen.gated)),
            "n_exits": 0 if getattr(screen, "exits", None) is None else int(len(screen.exits)),
            "n_charts": n_charts,
            "sector_lines": list(getattr(result, "sector_lines", []) or []),
            "etf_symbols": sorted((getattr(result, "etf_map", {}) or {}).keys()),
        }
        p = ui_dir() / f"last_batch_{market}.json"
        p.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
        if digest_text:
            (ui_dir() / f"digest_{market}_{result.date}.txt").write_text(digest_text, encoding="utf-8")
        return p
    except Exception as e:  # noqa: BLE001 — 기록 실패가 배치를 막지 않게
        print(f"[ui] 상태 기록 실패: {type(e).__name__}: {e}")
        return None


def record_failure(market: str, exc: BaseException) -> None:
    try:
        line = {"at": _now(), "market": str(market), "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
        with (ui_dir() / "failures.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def read_last_batches() -> dict[str, dict]:
    out: dict[str, dict] = {}
    d = ui_dir()
    for p in d.glob("last_batch_*.json"):
        try:
            info = json.loads(p.read_text(encoding="utf-8"))
            out[str(info.get("market") or p.stem.split("_")[-1])] = info
        except (OSError, json.JSONDecodeError):
            continue
    return out


def read_failures(limit: int = 20) -> list[dict]:
    p = ui_dir() / "failures.jsonl"
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for ln in reversed(lines):
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def list_digests(limit: int = 30) -> list[dict]:
    """최근 다이제스트 파일 목록 — [{name, market, date}] 최신순."""
    out = []
    for p in sorted(ui_dir().glob("digest_*.txt"), reverse=True)[:limit]:
        parts = p.stem.split("_")      # digest, MKT, date
        if len(parts) >= 3:
            out.append({"name": p.name, "market": parts[1], "date": parts[2]})
    return out


def read_digest(name: str) -> str:
    """파일명은 digest_*.txt 만 — 경로 조작 방지."""
    if not (name.startswith("digest_") and name.endswith(".txt") and "/" not in name and ".." not in name):
        return ""
    p = ui_dir() / name
    try:
        return p.read_text(encoding="utf-8") if p.exists() else ""
    except OSError:
        return ""
