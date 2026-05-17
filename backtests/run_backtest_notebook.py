"""
run_backtest_notebook.py — Jupyter Notebook 실행용 백테스트 코드
================================================================
이 파일을 Jupyter에서 %run으로 직접 실행하거나,
각 셀 구분(# %%) 기준으로 VS Code Jupyter에서 셀 단위 실행 가능.

실행 방법:
  방법 1) VS Code — 파일 열고 셀 단위 실행 (# %% 구분)
  방법 2) Jupyter Notebook — 각 셀을 복사해서 실행
  방법 3) %run backtests/run_backtest_notebook.py (전체 한번에)

실행 순서:
  셀 1: 경로 설정 + import
  셀 2: 패키지 확인
  셀 3: CSV 생성 (처음 1회, 이후 건너뛰기)
  셀 4: Walk-Forward 백테스트 실행
  셀 5: 결과 시각화
  셀 6: (선택) 레벨별 비교
  셀 7: (선택) 리포트 저장
"""

# %%
# ═══════════════════════════════════════════════════════════════
#  셀 1: 경로 설정 + import (여기만 수정)
# ═══════════════════════════════════════════════════════════════
import os
import sys

# 프로젝트 루트 (hello-world 폴더 경로)
PROJECT_DIR = r"D:\AutoTrade\hello-world"
sys.path.insert(0, PROJECT_DIR)
os.chdir(PROJECT_DIR)

# 데이터 경로
BASE_DIR       = r"D:\AutoTrade"
SIGNALS_CSV    = os.path.join(BASE_DIR, "Results", "cloud_scan_v2", "all", "signals_all.csv")
BTC_CSV        = os.path.join(BASE_DIR, "Raw_Data", "CRYPTO_BINANCE_15M", "BTC_USDT.csv")
OHLCV_DIR      = os.path.join(BASE_DIR, "Raw_Data", "CRYPTO_BINANCE_15M")
LABELED_CSV    = os.path.join(BASE_DIR, "Raw_Data", "labeled_signals", "signals_all_labeled.csv")

# 설정
WORKERS        = 24              # CPU 코어 수
SIGNAL_LEVEL   = "production"    # production / level1 / level2 / level3
MAX_SIGNALS    = None            # 빠른 테스트: 10000, 전체: None
SKIP_LABELING  = False           # 기존 라벨링 CSV 재사용 시 True

# 파일 확인
print("  [파일 확인]")
for name, path in [("시그널", SIGNALS_CSV), ("BTC", BTC_CSV), ("OHLCV", OHLCV_DIR)]:
    ok = "✅" if os.path.exists(path) else "❌"
    print(f"    {ok} {name}: {path}")

if os.path.exists(LABELED_CSV):
    size_mb = os.path.getsize(LABELED_CSV) / 1024 / 1024
    print(f"    ✅ 라벨링 CSV (기존): {LABELED_CSV} ({size_mb:.1f}MB)")
else:
    print(f"    ⚠  라벨링 CSV 없음 → 셀 3에서 생성")

print(f"\n  프로젝트: {PROJECT_DIR}")
print(f"  레벨: {SIGNAL_LEVEL}, 워커: {WORKERS}")


# %%
# ═══════════════════════════════════════════════════════════════
#  셀 2: 패키지 확인
# ═══════════════════════════════════════════════════════════════
try:
    import pandas as pd
    import numpy as np
    from pydantic import BaseModel
    print(f"  ✅ pandas {pd.__version__}, numpy {np.__version__}, pydantic OK")
except ImportError as e:
    print(f"  ⚠ 패키지 누락: {e}")
    print(f"  아래 실행 후 커널 재시작:")
    print(f"  !pip install pandas numpy pydantic")

try:
    import matplotlib.pyplot as plt
    print(f"  ✅ matplotlib {plt.matplotlib.__version__}")
except ImportError:
    print(f"  ⚠ matplotlib 없음 (차트 불가) → !pip install matplotlib")


# %%
# ═══════════════════════════════════════════════════════════════
#  셀 3: 라벨링 CSV 생성 (BTC 시장 상태 포함)
#  ⏱ 370만 시그널 기준 약 5~10분 (24코어)
#  ★ 처음 1회만 실행, 이후 이 셀은 건너뛰세요
# ═══════════════════════════════════════════════════════════════
from backtests.build_labeled_csv import build_labeled_csv

if SKIP_LABELING and os.path.exists(LABELED_CSV):
    # 기존 CSV에 BTC 피처만 추가 (라벨은 이미 있음)
    print("  기존 CSV에 BTC 시장 상태 피처 추가 중...")
    result_df = build_labeled_csv(
        signals_path=LABELED_CSV,
        btc_path=BTC_CSV,
        ohlcv_dir=OHLCV_DIR,
        output_path=LABELED_CSV,
        workers=WORKERS,
        skip_labeling=True,
    )
else:
    # 처음부터 전체 생성 (시그널 → 라벨링 → BTC 피처)
    print("  시그널 라벨링 + BTC 피처 전체 생성 중...")
    os.makedirs(os.path.dirname(LABELED_CSV), exist_ok=True)
    result_df = build_labeled_csv(
        signals_path=SIGNALS_CSV,
        btc_path=BTC_CSV,
        ohlcv_dir=OHLCV_DIR,
        output_path=LABELED_CSV,
        workers=WORKERS,
        skip_labeling=False,
    )

if result_df is not None:
    print(f"\n  ✅ 저장 완료: {LABELED_CSV}")
    print(f"  행 수: {len(result_df):,}")
    print(f"  컬럼: {list(result_df.columns)}")


# %%
# ═══════════════════════════════════════════════════════════════
#  셀 4: Walk-Forward 3윈도우 백테스트 실행
#  ⏱ production 기준 약 3~5분 (24코어)
# ═══════════════════════════════════════════════════════════════
from backtests.walk_forward import WalkForwardBacktest

bt = WalkForwardBacktest(
    data_path=LABELED_CSV,
    level=SIGNAL_LEVEL,
    modes=("baseline", "enhanced", "memory"),
    max_signals_per_window=MAX_SIGNALS,
    workers=WORKERS,
)

# 데이터 로드 + 레벨 필터
df = bt.load_data()
filtered = bt.filter_signals(df)
print(f"  필터 후: {len(filtered):,}건 (전체 {len(df):,}건, 레벨: {SIGNAL_LEVEL})")

# 3윈도우 순차 실행
results = bt.run_all_windows(filtered)

# 마크다운 리포트 출력
report = bt.generate_report()
print("\n" + report)


# %%
# ═══════════════════════════════════════════════════════════════
#  셀 5: 결과 시각화 (차트 4개)
# ═══════════════════════════════════════════════════════════════
import matplotlib.pyplot as plt
import matplotlib

# 한글 폰트 설정 (OS 자동 감지)
import platform
if platform.system() == 'Windows':
    matplotlib.rcParams['font.family'] = 'Malgun Gothic'
elif platform.system() == 'Darwin':
    matplotlib.rcParams['font.family'] = 'AppleGothic'
else:
    matplotlib.rcParams['font.family'] = 'DejaVu Sans'
matplotlib.rcParams['axes.unicode_minus'] = False

fig, axes = plt.subplots(2, 2, figsize=(14, 10))
fig.suptitle(f'Walk-Forward 백테스트 결과 (레벨: {SIGNAL_LEVEL})', fontsize=14, fontweight='bold')

windows = [r["window"] for r in results]
x = range(len(windows))
w = 0.25

# 데이터 추출
baseline_pf  = [r["baseline"].get("pf", 0) for r in results]
enhanced_pf  = [r["enhanced"].get("pf", 0) for r in results]
memory_pf    = [r["memory_enhanced"].get("pf", 0) for r in results]

baseline_wr  = [r["baseline"].get("winrate", 0) * 100 for r in results]
enhanced_wr  = [r["enhanced"].get("winrate", 0) * 100 for r in results]
memory_wr    = [r["memory_enhanced"].get("winrate", 0) * 100 for r in results]

baseline_roe = [r["baseline"].get("total_roe", 0) for r in results]
enhanced_roe = [r["enhanced"].get("total_roe", 0) for r in results]
memory_roe   = [r["memory_enhanced"].get("total_roe", 0) for r in results]

baseline_sl  = [r["baseline"].get("sl_hit_rate", 0) * 100 for r in results]
enhanced_sl  = [r["enhanced"].get("sl_hit_rate", 0) * 100 for r in results]
enhanced_rej = [r["enhanced"].get("rejection_rate", 0) * 100 for r in results]

# ── 1. PF 비교 ──
ax = axes[0, 0]
ax.bar([i - w for i in x], baseline_pf, w, label='A) Baseline', color='#6c757d')
ax.bar([i for i in x],     enhanced_pf, w, label='B) Enhanced', color='#0d6efd')
ax.bar([i + w for i in x], memory_pf,   w, label='C) Memory',   color='#198754')
ax.set_xticks(x)
ax.set_xticklabels(windows)
ax.set_ylabel('Profit Factor')
ax.set_title('PF 비교')
ax.axhline(y=1.0, color='red', linestyle='--', alpha=0.5, label='손익분기 (PF=1)')
ax.legend(fontsize=8)

# ── 2. 승률 비교 ──
ax = axes[0, 1]
ax.bar([i - w for i in x], baseline_wr, w, label='A) Baseline', color='#6c757d')
ax.bar([i for i in x],     enhanced_wr, w, label='B) Enhanced', color='#0d6efd')
ax.bar([i + w for i in x], memory_wr,   w, label='C) Memory',   color='#198754')
ax.set_xticks(x)
ax.set_xticklabels(windows)
ax.set_ylabel('승률 (%)')
ax.set_title('승률 비교')
ax.legend(fontsize=8)

# ── 3. 총 ROE 비교 ──
ax = axes[1, 0]
ax.bar([i - w for i in x], baseline_roe, w, label='A) Baseline', color='#6c757d')
ax.bar([i for i in x],     enhanced_roe, w, label='B) Enhanced', color='#0d6efd')
ax.bar([i + w for i in x], memory_roe,   w, label='C) Memory',   color='#198754')
ax.set_xticks(x)
ax.set_xticklabels(windows)
ax.set_ylabel('총 ROE (%)')
ax.set_title('총 ROE 비교')
ax.axhline(y=0, color='red', linestyle='--', alpha=0.5)
ax.legend(fontsize=8)

# ── 4. SL 히트율 & 거부율 ──
ax = axes[1, 1]
ax.bar([i - w for i in x], baseline_sl,  w, label='Baseline SL히트율', color='#dc3545', alpha=0.7)
ax.bar([i for i in x],     enhanced_sl,  w, label='Enhanced SL히트율', color='#fd7e14', alpha=0.7)
ax.bar([i + w for i in x], enhanced_rej, w, label='Enhanced 거부율',   color='#0dcaf0', alpha=0.7)
ax.set_xticks(x)
ax.set_xticklabels(windows)
ax.set_ylabel('비율 (%)')
ax.set_title('SL 히트율 & 거부율')
ax.legend(fontsize=8)

plt.tight_layout()
save_path = os.path.join(BASE_DIR, "Results", "backtest_walkforward.png")
os.makedirs(os.path.dirname(save_path), exist_ok=True)
plt.savefig(save_path, dpi=150, bbox_inches='tight')
plt.show()
print(f"  ✅ 차트 저장: {save_path}")


# %%
# ═══════════════════════════════════════════════════════════════
#  셀 6: (선택) 레벨별 비교 — 4개 레벨 한번에 실행
#  ⏱ 약 15~20분 (24코어)
# ═══════════════════════════════════════════════════════════════
from backtests.walk_forward import WalkForwardBacktest
import pandas as pd

levels = ["production", "level1", "level2", "level3"]
all_results = {}

for level in levels:
    print(f"\n{'#'*60}")
    print(f"  레벨: {level}")
    print(f"{'#'*60}")

    bt_lv = WalkForwardBacktest(
        data_path=LABELED_CSV,
        level=level,
        modes=("baseline", "enhanced", "memory"),
        max_signals_per_window=MAX_SIGNALS,
        workers=WORKERS,
    )
    df_raw = bt_lv.load_data()
    filtered_lv = bt_lv.filter_signals(df_raw)
    print(f"  필터 후: {len(filtered_lv):,}건")

    bt_lv.run_all_windows(filtered_lv)
    all_results[level] = bt_lv.results

# ── 요약 테이블 ──
summary_rows = []
for level, windows_data in all_results.items():
    for w_data in windows_data:
        summary_rows.append({
            "레벨": level,
            "윈도우": w_data["window"],
            "시그널수": w_data["test_signals"],
            "Baseline_PF": round(w_data["baseline"].get("pf", 0), 3),
            "Enhanced_PF": round(w_data["enhanced"].get("pf", 0), 3),
            "Memory_PF": round(w_data["memory_enhanced"].get("pf", 0), 3),
            "Baseline_승률": f"{w_data['baseline'].get('winrate', 0):.1%}",
            "Enhanced_승률": f"{w_data['enhanced'].get('winrate', 0):.1%}",
            "Memory_승률": f"{w_data['memory_enhanced'].get('winrate', 0):.1%}",
            "Enhanced_거부율": f"{w_data['enhanced'].get('rejection_rate', 0):.1%}",
        })

summary_df = pd.DataFrame(summary_rows)
display(summary_df)

# CSV 저장
summary_path = os.path.join(BASE_DIR, "Results", "backtest_level_comparison.csv")
os.makedirs(os.path.dirname(summary_path), exist_ok=True)
summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
print(f"\n  ✅ 레벨별 비교 저장: {summary_path}")


# %%
# ═══════════════════════════════════════════════════════════════
#  셀 7: (선택) 마크다운 리포트 파일 저장
# ═══════════════════════════════════════════════════════════════
from datetime import datetime

report_dir = os.path.join(BASE_DIR, "Results", "backtest_reports")
os.makedirs(report_dir, exist_ok=True)

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
report_path = os.path.join(report_dir, f"backtest_{SIGNAL_LEVEL}_{ts}.md")

with open(report_path, "w", encoding="utf-8") as f:
    f.write(report)

print(f"  ✅ 리포트 저장: {report_path}")
print(f"  내용 미리보기:")
print(report[:500])
