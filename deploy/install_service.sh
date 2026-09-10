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
# 파이썬이 해제한 메모리를 OS에 더 적극적으로 돌려준다(작은 VM에서 유효)
Environment=MALLOC_TRIM_THRESHOLD_=65536
ExecStart=${APP_DIR}/.venv/bin/python -m stock_auto.pipeline.run_daemon
Restart=always
RestartSec=30
# ── 작은 VM 보호 ──
# 실측 최대 사용량은 약 200MB다. 한도를 넉넉히 1GB로 두되, 넘으면
# 이 서비스만 종료·재시작되고 VM 전체가 멈추지는 않게 한다.
MemoryHigh=700M
MemoryMax=1G
# 배치가 CPU를 오래 쓰지 않지만, 다른 작업에 양보하도록 우선순위를 낮춘다
CPUWeight=50
Nice=5
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
