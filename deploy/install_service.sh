#!/usr/bin/env bash
# ============================================================
# systemd 등록 — 서버가 재부팅돼도 자동으로 다시 뜬다.
#   사용:  bash deploy/install_service.sh
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(whoami)"
SERVICE=/etc/systemd/system/stock-auto.service

echo "▶ 서비스 파일 생성: $SERVICE"
sudo tee "$SERVICE" >/dev/null <<UNIT
[Unit]
Description=Stock Auto - 미국장 추천 데몬
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python -m stock_auto.pipeline.run_daemon
Restart=always
RestartSec=30
StandardOutput=append:${APP_DIR}/logs/daemon.log
StandardError=append:${APP_DIR}/logs/daemon.log

[Install]
WantedBy=multi-user.target
UNIT

mkdir -p "${APP_DIR}/logs"
sudo systemctl daemon-reload
sudo systemctl enable stock-auto
sudo systemctl restart stock-auto
sleep 2
sudo systemctl status stock-auto --no-pager -l | head -20

cat <<MSG

────────────────────────────────────────────────
등록 완료.

 상태 보기    sudo systemctl status stock-auto
 로그 보기    tail -f ${APP_DIR}/logs/daemon.log
 중지         sudo systemctl stop stock-auto
 다시 시작    sudo systemctl restart stock-auto
 자동시작 해제 sudo systemctl disable stock-auto
────────────────────────────────────────────────
MSG
