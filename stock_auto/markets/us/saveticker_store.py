"""
세이브티커 수집 데이터 읽기 — PC 들이 git 저장소에 밀어 넣은 JSONL 을 합친다.

왜 이런 모양인가:
  saveticker.com 은 사이트 전체에 클라우드플레어 JS 챌린지가 걸려 있어 VM 에서
  직접 받을 수 없다(robots.txt 까지 403). 그래서 **사용자 PC 의 브라우저 확장**이
  페이지가 받는 /api/news/list · /api/news/top-stories 응답을 기록하고, 작은
  푸시 스크립트가 그것을 이 데이터 저장소에 올린다. VM 은 배치 직전에 pull 만
  한다. 사이트 입장에서는 사용자가 페이지를 읽는 것과 같은 접근이다.

여러 PC 중 하나만 켜져 있어도 되게:
  PC 마다 **자기 파일만** 쓴다 — data/<KST날짜>.<PC이름>.jsonl, append-only.
  같은 파일을 두 PC 가 건드리지 않으니 git 충돌이 원천적으로 없다. 같은 뉴스가
  여러 PC 파일에 들어오는 것은 당연하고, 여기서 id 로 합친다.

레코드(한 줄 = JSON 하나) — PC 푸시 스크립트(pc/saveticker_push.py)와 약속:
  {"id": 150966, "created_at": "2026-10-21T13:02:11Z",
   "title": "한글 제목", "title_en": "English title",
   "source": "Reuters", "tickers": ["NVDA", "AMD"], "labels": ["반도체"],
   "url": "https://www.saveticker.com/news/150966",
   "is_pick": true,                       # SAVE PICK(오늘 주요뉴스)
   "seen_at": "2026-10-21T13:03:00+09:00", "host": "desk-home"}

같은 id 가 여러 줄이면: seen_at 이 가장 늦은 줄의 제목(사이트가 원문 → 한글
번역으로 제목을 바꾼다)을 쓰고, is_pick 은 하나라도 true 면 true (올라온 뒤
몇 시간 지나 PICK 으로 뽑히기도 한다).
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DEFAULT_REPO = "sayoung001/saveticker-data"


@dataclass
class SaveItem:
    id: str
    created_at: str
    title: str
    title_en: str = ""
    source: str = ""
    tickers: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    url: str = ""
    is_pick: bool = False
    seen_at: str = ""
    host: str = ""

    @property
    def kst_date(self) -> str:
        try:
            dt = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
            return dt.astimezone(KST).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            return ""


# ──────────────────────────────────────────────────────────────────────────
# 위치·동기화
# ──────────────────────────────────────────────────────────────────────────
def repo_dir() -> Path:
    """클론 위치. 기본은 데이터 루트 아래 saveticker-data/."""
    env = os.environ.get("SAVETICKER_DATA_DIR", "").strip()
    if env:
        return Path(env)
    from stock_auto.config.paths import base_dir
    return base_dir() / "saveticker-data"


def _remote_url() -> Optional[str]:
    repo = os.environ.get("SAVETICKER_DATA_REPO", DEFAULT_REPO).strip()
    token = os.environ.get("SAVETICKER_DATA_TOKEN", "").strip()
    if not repo:
        return None
    if token:
        # 토큰은 .env 에만 있고, 여기서 만든 URL 은 클론의 .git/config 에 남는다.
        # VM 의 .env 와 같은 신뢰 경계 안이다(둘 다 600).
        return f"https://x-access-token:{token}@github.com/{repo}.git"
    return f"https://github.com/{repo}.git"      # 공개 저장소일 때만 동작


def sync(timeout: int = 60) -> dict:
    """배치 직전에 1회: 없으면 clone, 있으면 fast-forward pull.

    실패해도 예외를 올리지 않는다 — 뉴스가 없다고 추천이 멈추면 안 된다.
    반환: {"ok": bool, "action": "clone|pull|skip", "detail": str}
    """
    url = _remote_url()
    d = repo_dir()
    if not url:
        return {"ok": False, "action": "skip", "detail": "SAVETICKER_DATA_REPO 미설정"}
    if not (d / ".git").exists() and not os.environ.get("SAVETICKER_DATA_TOKEN", "").strip():
        # 토큰 없이 private 저장소를 clone 하면 "could not read Username" 만 남는다 — 뜻이 보이게
        return {"ok": False, "action": "skip",
                "detail": "SAVETICKER_DATA_TOKEN 미설정 — pc/README.md 0단계 (PC 수집 전까지는 뉴스 없이 진행)"}
    try:
        if not (d / ".git").exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["git", "clone", "--quiet", "--depth", "50", url, str(d)],
                               capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                return {"ok": False, "action": "clone",
                        "detail": _scrub(r.stderr.strip()[-300:])}
            return {"ok": True, "action": "clone", "detail": str(d)}
        r = subprocess.run(["git", "-C", str(d), "pull", "--quiet", "--ff-only"],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return {"ok": False, "action": "pull", "detail": _scrub(r.stderr.strip()[-300:])}
        return {"ok": True, "action": "pull", "detail": str(d)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "action": "error", "detail": f"{type(e).__name__}: {e}"}


def _scrub(s: str) -> str:
    """오류 문구에 토큰이 섞여 나오지 않게."""
    tok = os.environ.get("SAVETICKER_DATA_TOKEN", "").strip()
    return s.replace(tok, "***") if tok else s


# ──────────────────────────────────────────────────────────────────────────
# 읽기·병합
# ──────────────────────────────────────────────────────────────────────────
def _files_for(dates: Iterable[str]) -> list[Path]:
    d = repo_dir() / "data"
    if not d.exists():
        return []
    want = set(dates)
    out = []
    for p in sorted(d.glob("*.jsonl")):
        day = p.name.split(".", 1)[0]
        if day in want:
            out.append(p)
    return out


def _parse_line(line: str) -> Optional[SaveItem]:
    try:
        o = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(o, dict) or not o.get("id"):
        return None
    return SaveItem(
        id=str(o.get("id")), created_at=str(o.get("created_at") or ""),
        title=str(o.get("title") or "").strip(),
        title_en=str(o.get("title_en") or "").strip(),
        source=str(o.get("source") or "").strip(),
        tickers=[str(t).upper() for t in (o.get("tickers") or []) if t],
        labels=[str(t) for t in (o.get("labels") or []) if t],
        url=str(o.get("url") or ""),
        is_pick=bool(o.get("is_pick")),
        seen_at=str(o.get("seen_at") or ""), host=str(o.get("host") or ""),
    )


def load(dates: Iterable[str]) -> dict[str, SaveItem]:
    """날짜(KST) 목록의 파일을 전부 읽어 id 로 합친다."""
    merged: dict[str, SaveItem] = {}
    for p in _files_for(dates):
        try:
            with p.open(encoding="utf-8") as f:
                for line in f:
                    it = _parse_line(line)
                    if it is None:
                        continue
                    old = merged.get(it.id)
                    if old is None:
                        merged[it.id] = it
                        continue
                    # 늦게 본 줄이 최신 제목. PICK 은 한 번이라도 뽑혔으면 유지.
                    newer = it if it.seen_at >= old.seen_at else old
                    newer.is_pick = old.is_pick or it.is_pick
                    if not newer.title_en:
                        newer.title_en = old.title_en or it.title_en
                    merged[it.id] = newer
        except OSError as e:
            print(f"[saveticker] {p.name} 읽기 실패: {e}")
    return merged


def recent_dates(days: int = 2, now: Optional[datetime] = None) -> list[str]:
    """오늘(KST)부터 days 일치. 09:30 KST 배치가 보는 '간밤 미국장' 뉴스는
    KST 로는 오늘 새벽이므로 오늘·어제 두 날이면 충분하다."""
    now = now or datetime.now(KST)
    return [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]


def items_for(tickers: Iterable[str], days: int = 2,
              now: Optional[datetime] = None) -> dict[str, list[SaveItem]]:
    """종목별 항목(최신순). 태그가 있어 제목에서 추측할 필요가 없다."""
    want = {t.upper() for t in tickers if t}
    out: dict[str, list[SaveItem]] = {t: [] for t in want}
    for it in load(recent_dates(days, now)).values():
        for t in it.tickers:
            if t in want:
                out[t].append(it)
    for t in out:
        out[t].sort(key=lambda x: x.created_at, reverse=True)
    return out


def picks(days: int = 1, now: Optional[datetime] = None) -> list[SaveItem]:
    """SAVE PICK(오늘 주요뉴스) — 편집자가 고른 것. 종목 무관하게 전체."""
    items = [it for it in load(recent_dates(days, now)).values() if it.is_pick]
    items.sort(key=lambda x: x.created_at, reverse=True)
    return items


def summary(days: int = 2) -> str:
    d = repo_dir()
    if not (d / ".git").exists():
        return f"세이브티커 데이터 없음 ({d} — SAVETICKER_DATA_REPO/TOKEN 확인)"
    all_items = load(recent_dates(days))
    hosts = sorted({it.host for it in all_items.values() if it.host})
    n_pick = sum(1 for it in all_items.values() if it.is_pick)
    n_tag = sum(1 for it in all_items.values() if it.tickers)
    return (f"세이브티커 {len(all_items)}건(최근 {days}일) · PICK {n_pick} · "
            f"종목태그 {n_tag} · 수집PC {hosts or ['-']}")
