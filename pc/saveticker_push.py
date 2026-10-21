r"""
세이브티커 푸시 — PC 의 브라우저 확장이 모은 CSV 를 git 데이터 저장소에 올린다.

왜 PC 에서 하나:
  saveticker.com 은 클라우드플레어 JS 챌린지로 서버 접근을 막는다. 사람이 쓰는
  브라우저만 통과하므로, 수집은 PC 의 Edge/Chrome 확장(pc/extension)이 하고
  이 스크립트는 그 결과(Downloads\saveticker\*.csv)를 JSONL 로 바꿔 git 에
  밀어 넣는다. VM 은 배치 직전에 pull 만 한다.

여러 PC 중 하나만 켜져 있어도 되게:
  이 PC 는 **자기 이름이 붙은 파일만** 쓴다 — data/<KST날짜>.<PC이름>.jsonl.
  다른 PC 와 같은 파일을 건드리지 않으니 push 가 충돌하지 않는다. 같은 뉴스가
  여러 PC 파일에 겹치는 건 VM 이 id 로 합친다.

append-only 와 재기록:
  사이트는 원문 제목을 먼저 올리고 잠시 뒤 한글 번역으로 바꾸며, 올라온 지
  몇 시간 뒤에 SAVE PICK 으로 뽑기도 한다. 그래서 (제목, 영문제목, PICK) 지문이
  바뀐 항목은 **새 줄을 덧붙인다.** VM 은 seen_at 이 늦은 줄을 쓴다.
  지문은 .push_state/<PC이름>.json 에 두고 git 에는 넣지 않는다.

사용 (Windows PowerShell 또는 cmd):
  python pc\saveticker_push.py            # 10분마다 반복
  python pc\saveticker_push.py --once     # 한 번만
  python pc\saveticker_push.py --csv-dir "D:\Downloads\saveticker" --repo-dir "C:\st-data"

표준 라이브러리만 쓴다. git 은 PC 에 설치되어 있고 이 저장소에 push 권한이
있어야 한다(처음 push 때 브라우저 로그인 창이 뜬다).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
PICK_PREFIX = "[SAVE PICK]"
CSV_GLOB = "saveticker_news_*.csv"


def host_name() -> str:
    h = socket.gethostname().split(".")[0].lower()
    return re.sub(r"[^a-z0-9_-]+", "-", h).strip("-") or "pc"


def log(msg: str, logfile: Path | None = None) -> None:
    line = f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    if logfile:
        try:
            with logfile.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


# ──────────────────────────────────────────────────────────────────────────
# CSV → 레코드
# ──────────────────────────────────────────────────────────────────────────
def _kst_date(created_at: str) -> str:
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        return dt.astimezone(KST).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return ""


def _fingerprint(title: str, title_en: str, is_pick: bool) -> str:
    return hashlib.sha1(f"{title}\x1f{title_en}\x1f{int(is_pick)}".encode()).hexdigest()[:12]


def read_csv_rows(path: Path) -> list[dict]:
    """확장이 쓴 CSV. 헤더: time_kst,created_at,title,title_en,source,tickers,labels,url,id"""
    out = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if not (r.get("id") or "").strip():
                    continue
                out.append(r)
    except (OSError, csv.Error) as e:
        print(f"  ⚠️ {path.name} 읽기 실패: {e}")
    return out


def to_record(r: dict, host: str, now_iso: str) -> dict | None:
    title = (r.get("title") or "").strip()
    is_pick = title.startswith(PICK_PREFIX)
    if is_pick:
        title = title[len(PICK_PREFIX):].strip()
    created = (r.get("created_at") or "").strip()
    day = _kst_date(created)
    if not day:
        return None
    return {
        "id": str(r.get("id")).strip(),
        "created_at": created,
        "title": title,
        "title_en": (r.get("title_en") or "").strip(),
        "source": (r.get("source") or "").strip(),
        "tickers": [t for t in (r.get("tickers") or "").split() if t],
        "labels": [t for t in (r.get("labels") or "").split() if t],
        "url": (r.get("url") or "").strip(),
        "is_pick": is_pick,
        "seen_at": now_iso,
        "host": host,
        "_day": day,
    }


# ──────────────────────────────────────────────────────────────────────────
# 상태·쓰기
# ──────────────────────────────────────────────────────────────────────────
def load_state(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(p: Path, st: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def push_once(csv_dir: Path, repo_dir: Path, host: str, logfile: Path | None) -> int:
    """새/바뀐 항목을 JSONL 로 덧붙이고 커밋·푸시. 반환: 쓴 줄 수."""
    if not csv_dir.exists():
        log(f"CSV 폴더 없음: {csv_dir} — 확장이 아직 저장한 게 없거나 경로가 다릅니다", logfile)
        return 0
    if not (repo_dir / ".git").exists():
        log(f"git 저장소 아님: {repo_dir} — 먼저 clone 하세요", logfile)
        return 0

    state_path = repo_dir / ".push_state" / f"{host}.json"
    state = load_state(state_path)
    now_iso = datetime.now(KST).isoformat(timespec="seconds")

    files = sorted(csv_dir.glob(CSV_GLOB))
    # 최근 3일치만 본다 — 오래된 파일을 매번 다시 읽을 이유가 없다
    files = files[-3:]
    per_day: dict[str, list[dict]] = {}
    changed = 0
    for fp in files:
        for r in read_csv_rows(fp):
            rec = to_record(r, host, now_iso)
            if not rec:
                continue
            fpr = _fingerprint(rec["title"], rec["title_en"], rec["is_pick"])
            if state.get(rec["id"]) == fpr:
                continue
            state[rec["id"]] = fpr
            day = rec.pop("_day")
            per_day.setdefault(day, []).append(rec)
            changed += 1

    if not changed:
        # 지난 주기에 커밋은 됐는데 push 만 실패했을 수 있다 — 새 줄이 없어도 다시 민다
        if _unpushed(repo_dir):
            ok = _push_retry(repo_dir, logfile)
            log(f"{'✅' if ok else '⚠️'} 밀린 커밋 push {'완료' if ok else '실패'}", logfile)
        return 0

    data_dir = repo_dir / "data"
    data_dir.mkdir(exist_ok=True)
    touched = []
    for day, recs in per_day.items():
        out = data_dir / f"{day}.{host}.jsonl"
        with out.open("a", encoding="utf-8") as f:
            for rec in recs:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        touched.append(out.relative_to(repo_dir).as_posix())

    # .push_state 는 커밋하지 않는다
    gi = repo_dir / ".gitignore"
    if not gi.exists() or ".push_state/" not in gi.read_text(encoding="utf-8", errors="ignore"):
        with gi.open("a", encoding="utf-8") as f:
            f.write(".push_state/\n")
        touched.append(".gitignore")

    save_state(state_path, state)
    ok = git_push(repo_dir, touched, f"{host}: +{changed} ({', '.join(per_day)})", logfile)
    log(f"{'✅' if ok else '⚠️'} {changed}줄 기록 → {', '.join(touched)}"
        + ("" if ok else " (push 실패 — 다음 주기에 다시)"), logfile)
    return changed


def _git(repo: Path, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def git_push(repo: Path, paths: list[str], msg: str, logfile: Path | None) -> bool:
    r = _git(repo, "add", "--", *paths)
    if r.returncode != 0:
        log(f"git add 실패: {r.stderr.strip()[:200]}", logfile); return False
    r = _git(repo, "commit", "-q", "-m", msg)
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
        log(f"git commit 실패: {r.stderr.strip()[:200]}", logfile); return False
    return _push_retry(repo, logfile)


def _unpushed(repo: Path) -> bool:
    """upstream 보다 앞선 커밋이 있나 (upstream 이 없으면 로컬 커밋이 있는지)."""
    r = _git(repo, "rev-list", "--count", "@{u}..HEAD")
    if r.returncode == 0:
        return r.stdout.strip() not in ("", "0")
    return _git(repo, "rev-parse", "--verify", "-q", "HEAD").returncode == 0


def _push_retry(repo: Path, logfile: Path | None) -> bool:
    """내 파일만 건드리므로 rebase 가 충돌할 일이 없다. 3회 재시도."""
    for attempt in range(3):
        _git(repo, "pull", "--rebase", "--autostash", "-q")
        r = _git(repo, "push", "-q")
        if r.returncode != 0 and ("upstream" in r.stderr or "push.default" in r.stderr):
            # clone 직후 upstream 이 안 잡힌 경우 — 현재 브랜치를 같은 이름으로
            r = _git(repo, "push", "-q", "-u", "origin", "HEAD")
        if r.returncode == 0:
            return True
        log(f"push 실패({attempt + 1}/3): {r.stderr.strip()[-200:]}", logfile)
        time.sleep(5 * (attempt + 1))
    return False


# ──────────────────────────────────────────────────────────────────────────
def main() -> int:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    ap = argparse.ArgumentParser(description="세이브티커 CSV → git 데이터 저장소")
    ap.add_argument("--csv-dir", default=str(home / "Downloads" / "saveticker"),
                    help="확장이 CSV 를 저장하는 폴더")
    ap.add_argument("--repo-dir", default=str(home / "saveticker-data"),
                    help="saveticker-data 저장소 clone 위치")
    ap.add_argument("--interval", type=int, default=10, help="반복 주기(분)")
    ap.add_argument("--once", action="store_true", help="한 번만 실행")
    ap.add_argument("--host", default=host_name(), help="이 PC 이름(파일명에 붙음)")
    a = ap.parse_args()

    csv_dir, repo_dir = Path(a.csv_dir), Path(a.repo_dir)
    logfile = repo_dir / ".push_state" / "push.log"
    log(f"시작 — PC={a.host} csv={csv_dir} repo={repo_dir} "
        f"{'1회' if a.once else f'{a.interval}분마다'}", logfile)
    while True:
        try:
            push_once(csv_dir, repo_dir, a.host, logfile)
        except Exception as e:  # noqa: BLE001 — 한 번 실패가 루프를 죽이지 않게
            log(f"오류: {type(e).__name__}: {e}", logfile)
        if a.once:
            return 0
        time.sleep(max(1, a.interval) * 60)


if __name__ == "__main__":
    sys.exit(main())
