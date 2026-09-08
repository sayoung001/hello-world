#!/usr/bin/env bash
# ============================================================
# GCE VM 최초 1회 설치 스크립트 (Ubuntu 22.04 / 24.04)
#   사용:  bash deploy/setup_vm.sh
# 하는 일: 파이썬·가상환경·패키지 설치 → .env 틀 생성 → 자가점검
# ============================================================
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
echo "▶ 설치 위치: $APP_DIR"

echo "▶ [1/5] 시스템 패키지 설치"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip tzdata unzip

echo "▶ [2/5] 가상환경 생성 (.venv)"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip

echo "▶ [3/5] 파이썬 패키지 설치 (몇 분 걸립니다)"
pip install -q -r requirements.txt

echo "▶ [4/5] .env 준비"
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "   .env 를 만들었습니다. 값을 채워 넣으세요:  nano .env"
else
  chmod 600 .env
  echo "   .env 가 이미 있습니다 (건드리지 않음)"
fi

echo "▶ [5/5] 자가점검"
python -m stock_auto.tools.preflight || true

cat <<'MSG'

────────────────────────────────────────────────
설치 완료. 다음 순서로 진행하세요.

 1) 키 입력          nano .env          (저장: Ctrl+O → Enter, 종료: Ctrl+X)
 2) 연결 확인        source .venv/bin/activate
                     python -m stock_auto.tools.preflight --live
 3) 파이프라인 점검  python -m stock_auto.tools.check_pipeline
 4) 수동 1회 실행    python -m stock_auto.pipeline.run_daily_batch --market US
 5) 자동 실행 등록   bash deploy/install_service.sh
────────────────────────────────────────────────
MSG
