#!/usr/bin/env bash
# ============================================================
# 스왑 파일 만들기 — 2GB급 작은 VM의 보험
#   사용:  bash deploy/add_swap.sh        (기본 2GB)
#          bash deploy/add_swap.sh 1      (1GB)
#
# 왜 필요한가:
#   실측상 이 프로그램은 배치 최대 약 200MB만 쓴다(2GB의 12%). 즉 평소에는
#   스왑이 쓰이지 않는다. 다만 스왑이 아예 없으면 예기치 못한 급증(대형 응답,
#   라이브러리 업데이트 등)에서 리눅스가 프로세스를 즉시 죽인다(OOM Killer).
#   스왑이 있으면 느려질 뿐 죽지 않는다 — 값싼 보험이다.
# ============================================================
set -euo pipefail

SIZE_GB="${1:-2}"
SWAPFILE=/swapfile

if swapon --show | grep -q "$SWAPFILE"; then
  echo "이미 스왑이 켜져 있습니다:"
  swapon --show
  exit 0
fi

echo "▶ ${SIZE_GB}GB 스왑 파일 생성 (1~2분)"
sudo fallocate -l "${SIZE_GB}G" "$SWAPFILE" 2>/dev/null || \
  sudo dd if=/dev/zero of="$SWAPFILE" bs=1M count=$((SIZE_GB * 1024)) status=progress
sudo chmod 600 "$SWAPFILE"
sudo mkswap "$SWAPFILE"
sudo swapon "$SWAPFILE"

# 재부팅 후에도 유지
if ! grep -q "^${SWAPFILE}" /etc/fstab; then
  echo "${SWAPFILE} none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi

# 스왑은 '보험'이므로 웬만하면 쓰지 않게 한다(디스크는 느리다)
sudo sysctl -w vm.swappiness=10 >/dev/null
grep -q '^vm.swappiness' /etc/sysctl.conf || \
  echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf >/dev/null

echo "▶ 완료"
free -h
