#!/usr/bin/env bash
# ============================================================
# 깃허브에서 자동으로 당겨오기 — zip 업로드를 없앤다.
#
#   사용(수동):      bash deploy/autopull.sh
#   사용(자동):      bash deploy/install_autopull.sh  로 5분마다 실행 등록
#   알림 시험:       bash deploy/autopull.sh --test-notify
#
# 하는 일: 원격에 새 커밋이 있으면 pull → 패키지 갱신 → 데몬 재시작.
#          변경이 없으면 아무것도 하지 않고 조용히 끝난다.
#
# .env 와 data/ 는 추적 대상이 아니므로 pull 이 건드리지 않는다.
#
# ── 멈췄을 때 텔레그램 알림 ──
# 예전에는 실패해도 로그에만 남아서, 5분마다 조용히 실패하는 걸 알 방법이
# 없었다. 가장 고약한 경우는 "pull 은 됐는데 데몬 재시작이 막혀서 옛 코드가
# 계속 도는" 것이다 — 겉보기엔 반영된 것 같은데 결과가 안 바뀐다.
# 이제 중단 사유를 텔레그램으로 보낸다. 같은 사유로 5분마다 울리면 폰이
# 못 쓰게 되므로 REALERT_SEC(기본 6시간) 안에는 한 번만 보내고, 복구되면
# "복구" 알림을 한 번 보낸 뒤 상태를 지운다.
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
LOG="${APP_DIR}/logs/autopull.log"
STATE="${APP_DIR}/logs/autopull_alert.state"   # "<사유키> <epoch>"
BEAT="${APP_DIR}/logs/autopull.heartbeat"      # 매 실행마다 touch — 타이머 생존 확인용
ENV_FILE="${APP_DIR}/.env"
REALERT_SEC="${STOCK_AUTOPULL_REALERT_SEC:-21600}"   # 동일 사유 재알림 간격(초)
mkdir -p "$(dirname "$LOG")"

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# .env 에서 값 하나 읽기. 값 뒤의 인라인 주석(`KEY=5   # 설명`)을 잘라낸다.
# 토큰·chat_id 에는 공백이 없으므로 첫 공백에서 끊는 것으로 충분하다.
_env_val() {
  [ -f "$ENV_FILE" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" | head -1 \
    | sed -e 's/[[:space:]].*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

notify() {   # $1 = 한 줄 제목, $2 = 본문
  local tok cid
  tok="$(_env_val TELEGRAM_BOT_TOKEN)"
  cid="$(_env_val TELEGRAM_CHAT_ID)"
  if [ -z "$tok" ] || [ -z "$cid" ]; then
    say "⚠️  텔레그램 미설정(.env) — 알림을 보낼 수 없습니다"
    return 0
  fi
  # 알림 실패가 스크립트를 죽이지 않게 한다(이미 실패를 알리는 중일 수 있다)
  curl -sS -m 15 -o /dev/null -X POST \
    --data-urlencode "chat_id=${cid}" \
    --data-urlencode "text=$1"$'\n\n'"$2" \
    "https://api.telegram.org/bot${tok}/sendMessage" \
    || say "⚠️  텔레그램 전송 실패(네트워크?) — 로그만 남깁니다"
}

# 중단 — 사유를 알리고 종료. 같은 사유 반복은 억제한다.
fail() {   # $1 = 사유키, $2 = 사람이 읽을 설명
  trap - ERR                      # 알림 중 실패로 재귀하지 않게
  local key="$1" msg="$2" last_key="" last_ts=0 now
  now="$(date +%s)"
  if [ -f "$STATE" ]; then
    read -r last_key last_ts <"$STATE" 2>/dev/null || true
    [ -n "${last_ts:-}" ] || last_ts=0
  fi
  say "❌ $msg"
  if [ "$key" != "${last_key:-}" ] || [ "$(( now - last_ts ))" -ge "$REALERT_SEC" ]; then
    notify "🚨 자동 반영 멈춤 — $(hostname -s)" \
"$msg

이 상태로는 깃허브에 올린 수정이 서버에 반영되지 않습니다.
확인:  tail -30 ${APP_DIR}/logs/autopull.log"
    printf '%s %s\n' "$key" "$now" >"$STATE"
  else
    say "   (동일 사유 — ${REALERT_SEC}초 내 재알림 억제)"
  fi
  exit 1
}

# 직전에 실패한 적이 있으면 복구를 알린다.
recovered() {
  [ -f "$STATE" ] || return 0
  local key=""
  read -r key _ <"$STATE" 2>/dev/null || true
  say "✅ 이전 중단(${key:-?})에서 복구됨"
  notify "✅ 자동 반영 복구 — $(hostname -s)" \
"이전 중단 사유: ${key:-?}
지금은 정상입니다. 현재 커밋: $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
  rm -f "$STATE"
}

trap 'fail unexpected "예상치 못한 오류로 중단(autopull.sh ${LINENO}행). 로그를 확인하세요."' ERR

# ── 알림 배선 시험 ──
if [ "${1:-}" = "--test-notify" ]; then
  say "알림 시험 발송"
  notify "🔔 자동 반영 알림 시험 — $(hostname -s)" \
"이 메시지가 보이면 중단 알림이 정상 동작합니다.
현재 커밋: $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
  say "발송 완료 — 폰에서 확인하세요"
  exit 0
fi

: >>"$BEAT" && touch "$BEAT"     # 타이머가 살아 있다는 증거(변경 없는 날에도 갱신)

if [ ! -d .git ]; then
  fail notgit "git 저장소가 아닙니다. 최초 1회는 git clone 으로 받아야 합니다: git clone -b <브랜치> https://github.com/sayoung001/hello-world ~/auto_trade"
fi

BRANCH="${STOCK_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
if [ "$BRANCH" = "HEAD" ]; then
  fail detached "HEAD 가 분리(detached)되어 어느 브랜치를 따라갈지 알 수 없습니다. 고치기: git checkout <브랜치> 또는 .env 와 무관하게 STOCK_BRANCH 환경변수 지정"
fi

# 로컬에서 직접 고친 게 있으면 덮어쓰지 않고 멈춘다
if ! git diff --quiet || ! git diff --cached --quiet; then
  # 중단 사유는 '추적 파일의 수정'뿐이다. 추적 안 되는 파일까지 열거하면
  # 멀쩡한 파일을 범인으로 가리키게 되므로 -uno 로 제외한다.
  CHANGED="$(git status --short --untracked-files=no | head -10)"
  echo "$CHANGED" | tee -a "$LOG" >/dev/null
  fail localdirty "서버에서 직접 고친 파일이 있어 덮어쓰지 않고 멈췄습니다.
$CHANGED

버릴 거면: cd ${APP_DIR} && git checkout -- .
남길 거면: cd ${APP_DIR} && git stash"
fi

if ! git fetch --quiet origin "$BRANCH" 2>>"$LOG"; then
  fail fetch "깃허브에서 받아오기 실패(${BRANCH}). 네트워크나 인증을 확인하세요."
fi

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/${BRANCH}")"

if [ "$LOCAL" = "$REMOTE" ]; then
  recovered                      # 고쳐졌으면 여기서 복구 알림이 간다
  exit 0                         # 변경 없음 — 조용히 종료
fi

say "새 커밋 발견 ${LOCAL:0:7} → ${REMOTE:0:7}"
git log --oneline "${LOCAL}..${REMOTE}" | head -10 | tee -a "$LOG"

if ! git merge --ff-only "origin/${BRANCH}" >>"$LOG" 2>&1; then
  fail notff "이력이 갈라져 fast-forward 할 수 없습니다(서버에서 커밋을 만든 경우). 수동 확인이 필요합니다: cd ${APP_DIR} && git status && git log --oneline -3"
fi

# requirements.txt 가 바뀌었을 때만 pip 실행
if git diff --name-only "${LOCAL}..${REMOTE}" | grep -q '^requirements.txt$'; then
  say "requirements.txt 변경 → 패키지 갱신"
  if ! "${APP_DIR}/.venv/bin/pip" install -q -r requirements.txt >>"$LOG" 2>&1; then
    fail pip "코드는 받았지만 패키지 갱신(pip)에 실패했습니다. 새 라이브러리가 필요한 코드라면 배치가 깨질 수 있습니다."
  fi
fi

# 문서만 바뀐 경우에는 데몬을 건드리지 않는다
if git diff --name-only "${LOCAL}..${REMOTE}" | grep -qv -e '\.md$' -e '^docs/'; then
  if systemctl is-active --quiet stock-auto 2>/dev/null; then
    say "코드 변경 → 데몬 재시작"
    # -n(비대화형): 타이머에는 tty 가 없어 비밀번호를 물으면 그대로 멈춘다.
    if ! sudo -n systemctl restart stock-auto 2>>"$LOG"; then
      fail restart "코드는 받았지만 데몬 재시작이 막혔습니다 — 서버가 계속 옛 코드로 돕니다.
비밀번호 없는 sudo 를 등록하세요:
  echo \"\$(whoami) ALL=(ALL) NOPASSWD: /bin/systemctl restart stock-auto\" | sudo tee /etc/sudoers.d/stock-auto
  sudo chmod 440 /etc/sudoers.d/stock-auto"
    fi
  else
    say "데몬이 돌고 있지 않음 — 재시작 생략"
  fi
else
  say "문서만 변경 → 데몬 유지"
fi

say "✅ 반영 완료 (${REMOTE:0:7})"
recovered
