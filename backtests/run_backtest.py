"""
run_backtest.py — 백테스트 실행 스크립트 (VS Code / Windows)
=============================================================
1단계: build_labeled_csv.py로 BTC 피처 포함 CSV 생성
2단계: walk_forward.py로 3윈도우 백테스트 실행

사용법 (VS Code 터미널에서):
  python backtests/run_backtest.py

설정을 변경하려면 아래 CONFIG 섹션을 수정하세요.
"""

from __future__ import annotations
import os
import sys
import time

# ═══════════════════════════════════════════════════════════════
#  CONFIG — 여기만 수정하세요
# ═══════════════════════════════════════════════════════════════

# 기본 디렉토리 (D:/AutoTrade 또는 사용자 환경에 맞게 변경)
BASE_DIR = os.environ.get("AUTOTRADE_DIR", r"D:\AutoTrade")

# 입력 파일 경로
SIGNALS_CSV = os.path.join(BASE_DIR, "Results", "cloud_scan_v2", "all", "signals_all.csv")
BTC_CSV = os.path.join(BASE_DIR, "Raw_Data", "CRYPTO_BINANCE_15M", "BTC_USDT.csv")
OHLCV_DIR = os.path.join(BASE_DIR, "Raw_Data", "CRYPTO_BINANCE_15M")

# 출력 파일 경로
LABELED_CSV = os.path.join(BASE_DIR, "Raw_Data", "labeled_signals", "signals_all_labeled.csv")
REPORT_DIR = os.path.join(BASE_DIR, "Results", "backtest_reports")

# 워커 수 (CPU 코어 수에 맞게)
WORKERS = 24

# 시그널 레벨 ("production", "level1", "level2", "level3")
SIGNAL_LEVEL = "production"

# 윈도우당 최대 시그널 수 (None = 제한 없음, 빠른 테스트는 10000 등)
MAX_SIGNALS = None

# 이미 라벨링된 CSV가 있으면 라벨링 건너뛰기
SKIP_LABELING = False

# 평가 모드 ("baseline", "enhanced", "memory" 중 선택, 쉼표 구분)
MODES = "baseline,enhanced,memory"

# ═══════════════════════════════════════════════════════════════


def check_files():
    """입력 파일 존재 확인"""
    print(f"\n{'='*70}")
    print(f"  파일 확인")
    print(f"{'='*70}")

    files = {
        "시그널 CSV": SIGNALS_CSV,
        "BTC 15분봉": BTC_CSV,
        "OHLCV 디렉토리": OHLCV_DIR,
    }

    all_ok = True
    for name, path in files.items():
        exists = os.path.exists(path)
        status = "✅" if exists else "❌"
        print(f"  {status} {name}: {path}")
        if not exists:
            all_ok = False

    if SKIP_LABELING and os.path.exists(LABELED_CSV):
        print(f"  ✅ 라벨링 CSV (기존): {LABELED_CSV}")
    elif SKIP_LABELING:
        print(f"  ❌ 라벨링 CSV 없음: {LABELED_CSV}")
        print(f"     → SKIP_LABELING = False로 변경하세요")
        all_ok = False

    return all_ok


def step1_build_csv():
    """1단계: 라벨링 + BTC 피처 CSV 생성"""
    from backtests.build_labeled_csv import build_labeled_csv

    print(f"\n{'='*70}")
    print(f"  1단계: 라벨링 CSV 생성")
    print(f"{'='*70}")

    if SKIP_LABELING and os.path.exists(LABELED_CSV):
        print(f"  기존 CSV 사용 + BTC 피처 보강")
        build_labeled_csv(
            signals_path=LABELED_CSV,
            btc_path=BTC_CSV,
            ohlcv_dir=OHLCV_DIR,
            output_path=LABELED_CSV,
            workers=WORKERS,
            skip_labeling=True,
        )
    else:
        build_labeled_csv(
            signals_path=SIGNALS_CSV,
            btc_path=BTC_CSV,
            ohlcv_dir=OHLCV_DIR,
            output_path=LABELED_CSV,
            workers=WORKERS,
            skip_labeling=False,
        )


def step2_walk_forward():
    """2단계: Walk-Forward 백테스트 실행"""
    from backtests.walk_forward import WalkForwardBacktest
    import pandas as pd

    print(f"\n{'='*70}")
    print(f"  2단계: Walk-Forward 백테스트")
    print(f"{'='*70}")
    print(f"  데이터: {LABELED_CSV}")
    print(f"  레벨: {SIGNAL_LEVEL}")
    print(f"  모드: {MODES}")
    print(f"  워커: {WORKERS}")
    if MAX_SIGNALS:
        print(f"  윈도우당 최대: {MAX_SIGNALS:,}")

    modes_tuple = tuple(m.strip() for m in MODES.split(","))

    bt = WalkForwardBacktest(
        data_path=LABELED_CSV,
        level=SIGNAL_LEVEL,
        modes=modes_tuple,
        max_signals_per_window=MAX_SIGNALS,
        workers=WORKERS,
    )

    df = bt.load_data()
    df = bt.filter_signals(df)
    print(f"  필터 후: {len(df):,}건 (레벨: {SIGNAL_LEVEL})")

    bt.run_all_windows(df)

    # 리포트 저장
    os.makedirs(REPORT_DIR, exist_ok=True)
    report = bt.generate_report()

    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(REPORT_DIR, f"backtest_{SIGNAL_LEVEL}_{timestamp}.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n  리포트 저장: {report_path}")

    return bt.results


def main():
    print(f"\n{'#'*70}")
    print(f"  Auto Trade — Walk-Forward 백테스트")
    print(f"  레벨: {SIGNAL_LEVEL} | 모드: {MODES}")
    print(f"{'#'*70}")

    # 파일 확인
    if not check_files():
        print(f"\n  ❌ 입력 파일 확인 실패. CONFIG 섹션의 경로를 수정하세요.")
        sys.exit(1)

    t0 = time.time()

    # 1단계
    step1_build_csv()

    # 2단계
    results = step2_walk_forward()

    elapsed = time.time() - t0
    print(f"\n{'#'*70}")
    print(f"  전체 완료: {elapsed/60:.1f}분")
    print(f"{'#'*70}")


if __name__ == "__main__":
    # Windows 멀티프로세싱 안전장치
    import __main__
    if not hasattr(__main__, "__spec__"):
        __main__.__spec__ = None

    main()
