#!/usr/bin/env bash
# ============================================================
# zip을 새로 올렸을 때 반영하는 스크립트
#   사용:  bash deploy/update.sh ~/stock_auto_new.zip
# .env 와 data/ 는 보존한다.
# ============================================================
set -euo pipefail

ZIP="${1:?사용법: bash deploy/update.sh <새 zip 경로>}"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"

echo "▶ 데몬 중지"
sudo systemctl stop stock-auto 2>/dev/null || true

echo "▶ 현재 코드 백업 → ${APP_DIR}.bak_${STAMP}"
cp -a "$APP_DIR" "${APP_DIR}.bak_${STAMP}"

echo "▶ 새 코드 전개 (.env / data / logs / .venv 는 유지)"
TMP="$(mktemp -d)"
unzip -q "$ZIP" -d "$TMP"
SRC="$TMP"
# zip 안에 폴더가 한 겹 더 있으면 그 안으로 들어간다
if [ "$(ls -1 "$TMP" | wc -l)" -eq 1 ] && [ -d "$TMP/$(ls -1 "$TMP")" ]; then
  SRC="$TMP/$(ls -1 "$TMP")"
fi
rsync -a --delete \
  --exclude '.env' --exclude 'data/' --exclude 'logs/' --exclude '.venv/' \
  "$SRC"/ "$APP_DIR"/
rm -rf "$TMP"

echo "▶ 패키지 갱신"
cd "$APP_DIR"
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

echo "▶ 점검 후 재시작"
python -m stock_auto.tools.preflight || true
sudo systemctl start stock-auto 2>/dev/null || true
echo "완료. 백업: ${APP_DIR}.bak_${STAMP}"
