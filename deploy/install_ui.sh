#!/usr/bin/env bash
# ============================================================
# PC 운영 화면(stock-ui) systemd 등록 — VM 안에서만 듣는다(127.0.0.1:8787).
#   사용:  bash deploy/install_ui.sh
#   PC 에서 보기:  gcloud compute ssh <VM> -- -L 8787:127.0.0.1:8787
#                 → 브라우저 http://localhost:8787
#   포트 변경:   .env 에 STOCK_UI_PORT=xxxx
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(whoami)"
SERVICE=/etc/systemd/system/stock-ui.service

echo "▶ 서비스 파일 생성: $SERVICE"
sudo tee "$SERVICE" >/dev/null <<UNIT
[Unit]
Description=Stock Auto - PC 운영 화면(로컬 HTTP)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER_NAME}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python -m stock_auto.ui.serve
Restart=always
RestartSec=10
MemoryMax=300M
Nice=10
StandardOutput=append:${APP_DIR}/logs/ui.log
StandardError=append:${APP_DIR}/logs/ui.log

[Install]
WantedBy=multi-user.target
UNIT

mkdir -p "${APP_DIR}/logs"
sudo systemctl daemon-reload
sudo systemctl enable stock-ui
sudo systemctl restart stock-ui
sleep 2
sudo systemctl status stock-ui --no-pager -l | head -12

cat <<MSG

────────────────────────────────────────────────
등록 완료. VM 안에서만 듣습니다(외부 노출 없음).

 PC 에서 열기 (터미널 하나 띄워 두고):
   gcloud compute ssh $(hostname) -- -L 8787:127.0.0.1:8787
   브라우저  http://localhost:8787

 상태 보기    sudo systemctl status stock-ui
 로그 보기    tail -f ${APP_DIR}/logs/ui.log
 중지         sudo systemctl stop stock-ui
────────────────────────────────────────────────
MSG
