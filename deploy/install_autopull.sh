#!/usr/bin/env bash
# ============================================================
# autopull 을 5분마다 돌리도록 systemd timer 등록
#   사용:  bash deploy/install_autopull.sh [분]
# ============================================================
set -euo pipefail

MINUTES="${1:-5}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(whoami)"

sudo tee /etc/systemd/system/stock-autopull.service >/dev/null <<UNIT
[Unit]
Description=Stock Auto - 깃허브 자동 반영
After=network-online.target

[Service]
Type=oneshot
User=${USER_NAME}
WorkingDirectory=${APP_DIR}
ExecStart=/bin/bash ${APP_DIR}/deploy/autopull.sh
UNIT

sudo tee /etc/systemd/system/stock-autopull.timer >/dev/null <<UNIT
[Unit]
Description=Stock Auto - ${MINUTES}분마다 깃허브 확인

[Timer]
OnBootSec=2min
OnUnitActiveSec=${MINUTES}min
AccuracySec=30s

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now stock-autopull.timer
sleep 1
systemctl list-timers stock-autopull.timer --no-pager | head -4

cat <<MSG

────────────────────────────────────────────────
등록 완료 — ${MINUTES}분마다 깃허브를 확인해 새 커밋을 반영합니다.

 즉시 1회 실행   sudo systemctl start stock-autopull.service
 로그            tail -f ${APP_DIR}/logs/autopull.log
 중지            sudo systemctl disable --now stock-autopull.timer

※ 데몬이 sudo systemctl restart 를 하려면 비밀번호 없는 sudo 가 필요합니다.
   안 되면 아래 한 줄을 추가하세요 (visudo):
   ${USER_NAME} ALL=(ALL) NOPASSWD: /bin/systemctl restart stock-auto
────────────────────────────────────────────────
MSG
