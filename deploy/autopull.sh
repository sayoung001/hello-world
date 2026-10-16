#!/usr/bin/env bash
# ============================================================
# 깃허브에서 자동으로 당겨오기 — zip 업로드를 없앤다.
#
#   사용(수동):  bash deploy/autopull.sh
#   사용(자동):  bash deploy/install_autopull.sh  로 5분마다 실행 등록
#
# 하는 일: 원격에 새 커밋이 있으면 pull → 패키지 갱신 → 데몬 재시작.
#          변경이 없으면 아무것도 하지 않고 조용히 끝난다.
#
# .env 와 data/ 는 추적 대상이 아니므로 pull 이 건드리지 않는다.
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
BRANCH="${STOCK_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
LOG="${APP_DIR}/logs/autopull.log"
mkdir -p "$(dirname "$LOG")"

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

if [ ! -d .git ]; then
  say "❌ git 저장소가 아닙니다. 최초 1회는 git clone 으로 받아야 합니다:"
  say "   git clone -b ${BRANCH} https://github.com/sayoung001/hello-world ~/app"
  exit 1
fi

# 로컬에서 직접 고친 게 있으면 덮어쓰지 않고 멈춘다
if ! git diff --quiet || ! git diff --cached --quiet; then
  say "⚠️  로컬 변경이 있어 중단합니다. 먼저 정리하세요:"
  git status --short | tee -a "$LOG"
  say "   (버릴 거면: git checkout -- . / 남길 거면: git stash)"
  exit 1
fi

git fetch --quiet origin "$BRANCH"
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/${BRANCH}")

if [ "$LOCAL" = "$REMOTE" ]; then
  exit 0                      # 변경 없음 — 조용히 종료(로그도 남기지 않음)
fi

say "새 커밋 발견 ${LOCAL:0:7} → ${REMOTE:0:7}"
git log --oneline "${LOCAL}..${REMOTE}" | head -10 | tee -a "$LOG"

git merge --ff-only "origin/${BRANCH}" >>"$LOG" 2>&1 || {
  say "❌ fast-forward 불가(이력이 갈라짐). 수동 확인이 필요합니다."
  exit 1
}

# requirements.txt 가 바뀌었을 때만 pip 실행
if git diff --name-only "${LOCAL}..${REMOTE}" | grep -q '^requirements.txt$'; then
  say "requirements.txt 변경 → 패키지 갱신"
  "${APP_DIR}/.venv/bin/pip" install -q -r requirements.txt >>"$LOG" 2>&1 || \
    say "⚠️  pip 갱신 실패 — 로그 확인"
fi

# 문서만 바뀐 경우에는 데몬을 건드리지 않는다
if git diff --name-only "${LOCAL}..${REMOTE}" | grep -qv -e '\.md$' -e '^docs/'; then
  if systemctl is-active --quiet stock-auto 2>/dev/null; then
    say "코드 변경 → 데몬 재시작"
    sudo systemctl restart stock-auto
  fi
else
  say "문서만 변경 → 데몬 유지"
fi

say "✅ 반영 완료 (${REMOTE:0:7})"
