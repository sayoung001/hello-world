#!/usr/bin/env bash
# ============================================================
# VM 현재 상태 스냅샷 — git 클론으로 바꾸기 전에 반드시 1회.
#
#   사용:  bash deploy/snapshot_vm.sh
#
# 왜: ~/app 을 fresh clone 으로 교체하면 VM에서 직접 고친 코드가 사라진다.
#     이 스크립트는 (1) 전체 백업 tar 를 만들고
#                  (2) 깃허브 버전과 다른 파일 목록·diff 를 뽑는다.
#     diff 가 비어 있으면 안심하고 교체해도 된다.
# ============================================================
set -euo pipefail

APP_DIR="${1:-$HOME/app}"
BRANCH="${STOCK_BRANCH:-claude/trusting-heisenberg-387xeq}"
REPO="https://github.com/sayoung001/hello-world"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$HOME/vm_snapshot_${STAMP}"
mkdir -p "$OUT"

echo "▶ 대상: $APP_DIR"
[ -d "$APP_DIR" ] || { echo "❌ $APP_DIR 가 없습니다. 경로를 인자로 주세요."; exit 1; }

echo "▶ [1/4] 전체 백업 (.venv 제외)"
tar czf "$OUT/app_backup.tar.gz" -C "$(dirname "$APP_DIR")" \
    --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
    "$(basename "$APP_DIR")"
echo "   $(du -h "$OUT/app_backup.tar.gz" | cut -f1)"

echo "▶ [2/4] 환경 정보 수집"
{
  echo "=== 수집 시각 ==="; date -Is
  echo; echo "=== 호스트 ==="; hostname; uname -a
  echo; echo "=== 파이썬 ==="; (${APP_DIR}/.venv/bin/python -V 2>&1 || python3 -V)
  echo; echo "=== 설치 패키지 ==="
  (${APP_DIR}/.venv/bin/pip freeze 2>/dev/null || echo "(venv 없음)")
  echo; echo "=== systemd 유닛 ==="
  systemctl list-units --type=service --all 'stock*' --no-pager 2>/dev/null || true
  systemctl list-timers 'stock*' --no-pager 2>/dev/null || true
  echo; echo "=== cron ==="; crontab -l 2>/dev/null || echo "(없음)"
  echo; echo "=== .env 키 이름만 (값은 제외) ==="
  [ -f "$APP_DIR/.env" ] && grep -oE '^[A-Z_]+' "$APP_DIR/.env" | sort || echo "(.env 없음)"
  echo; echo "=== 데이터 현황 ==="
  du -sh "$APP_DIR"/data/* 2>/dev/null || echo "(data 없음)"
  echo; echo "=== 신호 기록 건수 ==="
  f="$APP_DIR/data/tracking/signals.csv"
  [ -f "$f" ] && echo "$(($(wc -l < "$f") - 1)) 건" || echo "(없음)"
} > "$OUT/environment.txt" 2>&1
echo "   → environment.txt"

echo "▶ [3/4] 깃허브 버전과 비교"
git clone -q --depth 1 -b "$BRANCH" "$REPO" "$OUT/github_ref" 2>/dev/null || {
  echo "   ⚠️  클론 실패(네트워크?) — 비교를 건너뜁니다"; BRANCH=""; }

if [ -n "$BRANCH" ]; then
  # 추적 대상 파일만 비교 (.env·data·venv·로그 제외)
  diff -ru -x '.git' -x '.venv' -x '__pycache__' -x 'data' -x 'logs' \
       -x '.env' -x '*.pyc' \
       "$OUT/github_ref" "$APP_DIR" > "$OUT/diff_vs_github.txt" 2>&1 || true
  # 세 종류로 나눠 본다. 중요도는 ① > ② > ③ 순이다.
  #   ① VM에만 있는 파일  — 직접 만든 것. 교체하면 사라진다
  #   ② 내용이 다른 파일  — 직접 고친 것. 교체하면 사라진다
  #   ③ 깃허브에만 있는 파일 — 아직 배포 안 된 것. 교체하면 들어온다(정상)
  {
    echo "=== 깃허브($BRANCH) 대비 차이 ==="
    echo
    echo "① VM에만 있는 파일 — 교체하면 사라집니다 ★확인 필수"
    grep "^Only in ${APP_DIR}" "$OUT/diff_vs_github.txt" 2>/dev/null \
      | sed "s|^Only in ${APP_DIR}/*||; s|^: ||; s|: |/|; s|^|  |" || true
    grep -q "^Only in ${APP_DIR}" "$OUT/diff_vs_github.txt" 2>/dev/null || echo "  (없음)"
    echo
    echo "② 내용이 다른 파일 — 교체하면 깃허브 버전으로 덮입니다 ★확인 필수"
    # "diff -ru <플래그...> REF/경로 APP/경로" — 끝에서 두 번째 필드가 REF 경로다
    grep '^diff -ru' "$OUT/diff_vs_github.txt" 2>/dev/null \
      | awk '{print $(NF-1)}' | sed "s|^${OUT}/github_ref/*|  |" || true
    grep -q '^diff -ru' "$OUT/diff_vs_github.txt" 2>/dev/null || echo "  (없음)"
    echo
    echo "③ 깃허브에만 있는 파일 — 교체하면 새로 들어옵니다 (정상)"
    grep "^Only in ${OUT}/github_ref" "$OUT/diff_vs_github.txt" 2>/dev/null \
      | sed "s|^Only in ${OUT}/github_ref/*||; s|^: ||; s|: |/|; s|^|  |" | head -40 || true
    grep -q "^Only in ${OUT}/github_ref" "$OUT/diff_vs_github.txt" 2>/dev/null || echo "  (없음)"
  } > "$OUT/diff_summary.txt"
  N_VM=$(grep -c "^Only in ${APP_DIR}" "$OUT/diff_vs_github.txt" 2>/dev/null || true)
  N_MOD=$(grep -c '^diff -ru' "$OUT/diff_vs_github.txt" 2>/dev/null || true)
  echo "   → diff_summary.txt · diff_vs_github.txt (전문)"
  echo "      VM에만 있는 것 ${N_VM:-0}건 · 내용 다른 것 ${N_MOD:-0}건"
  if [ "${N_VM:-0}" -gt 0 ] || [ "${N_MOD:-0}" -gt 0 ]; then
    echo "      ⚠️  VM 쪽 변경이 있습니다. diff_summary.txt 의 ①②를 먼저 확인하세요."
  else
    echo "      ✅ VM 쪽 변경 없음 — 안심하고 교체 가능"
  fi
fi

echo "▶ [4/4] 로그 최근분 보관"
mkdir -p "$OUT/logs"
for f in "$APP_DIR"/logs/*.log; do
  [ -f "$f" ] && tail -500 "$f" > "$OUT/logs/$(basename "$f")"
done 2>/dev/null || true

rm -rf "$OUT/github_ref"
echo
echo "────────────────────────────────────────────────"
echo "스냅샷 완료: $OUT"
ls -la "$OUT"
echo
echo "다음: 아래 두 파일을 확인하세요."
echo "  cat $OUT/diff_summary.txt      ← ①②가 '(없음)'이면 안심하고 교체 가능"
echo "  cat $OUT/environment.txt"
echo "────────────────────────────────────────────────"
