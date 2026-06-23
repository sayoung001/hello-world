"""
build_labeled_csv.py — 백테스트용 라벨링 CSV 생성기
=====================================================
cloud_scan_v2.py 시그널 + BTC 15분봉 → walk_forward.py가 기대하는 포맷으로 변환.

생성 컬럼:
  [시그널 원본]  symbol, direction, confidence, gap, adx, entry_price,
                stop_loss, take_profit, atr, squeeze_candles, ...
  [라벨링 결과]  exit_type, exit_price, realized_roe, hold_candles,
                mae_pct, mfe_pct, direction_correct
  [BTC 시장상태]  btc_price, btc_level, btc_atr_pct, btc_rsi,
                  btc_change_1h, btc_change_4h, btc_change_24h
  [메타]        timestamp, scan_gap

사용법:
  python backtests/build_labeled_csv.py ^
      --signals D:/AutoTrade/Results/cloud_scan_v2/all/signals_all.csv ^
      --btc     D:/AutoTrade/Raw_Data/CRYPTO_BINANCE_15M/BTC_USDT.csv ^
      --ohlcv   D:/AutoTrade/Raw_Data/CRYPTO_BINANCE_15M/ ^
      --output  D:/AutoTrade/Raw_Data/labeled_signals/signals_all_labeled.csv ^
      --workers 24

필수 입력:
  --signals: cloud_scan_v2.py의 signals_all.csv (또는 개별 GAP CSV)
  --btc:     BTC_USDT 15분봉 CSV (Date,Open,High,Low,Close,Volume)
  --ohlcv:   코인별 OHLCV 디렉토리 (코인명_USDT.csv)
  --output:  출력 경로
"""

from __future__ import annotations
import os
import sys
import argparse
import time
import math
import multiprocessing as mp
from datetime import datetime

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════
#  BTC 시장 상태 계산
# ═══════════════════════════════════════════════════════════════

def compute_btc_features(btc_path: str) -> pd.DataFrame:
    """
    BTC 15분봉에서 시장 상태 피처를 계산.
    시그널 timestamp 기준으로 merge할 수 있도록 인덱스=Date.
    """
    print(f"  BTC 데이터 로드: {btc_path}")
    df = pd.read_csv(btc_path)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    c = df["Close"].values.astype(float)
    h = df["High"].values.astype(float)
    lo = df["Low"].values.astype(float)
    n = len(c)

    # EMA 12/26
    ema_s = pd.Series(c).ewm(span=12).mean().values
    ema_l = pd.Series(c).ewm(span=26).mean().values

    # ATR(14)
    prev_c = np.roll(c, 1); prev_c[0] = c[0]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_c), np.abs(lo - prev_c)))
    tr[0] = h[0] - lo[0]
    atr = pd.Series(tr).rolling(14).mean().values
    btc_atr_pct = atr / (c + 1e-9) * 100

    # ADX(14)
    plus_dm = np.diff(h, prepend=h[0])
    minus_dm = -np.diff(lo, prepend=lo[0])
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0.0)
    minus_dm_v = np.where((minus_dm > np.diff(h, prepend=h[0])) & (minus_dm > 0), minus_dm, 0.0)
    sa = pd.Series(atr).rolling(14).mean().values + 1e-9
    pdi = 100 * pd.Series(plus_dm).rolling(14).mean().values / sa
    mdi = 100 * pd.Series(minus_dm_v).rolling(14).mean().values / sa
    dx = 100 * np.abs(pdi - mdi) / (pdi + mdi + 1e-9)
    adx = pd.Series(dx).rolling(14).mean().values

    # RSI(14)
    delta = np.diff(c, prepend=c[0])
    gain = pd.Series(np.where(delta > 0, delta, 0.0)).rolling(14).mean().values
    loss = pd.Series(np.where(delta < 0, -delta, 0.0)).rolling(14).mean().values + 1e-9
    rsi = 100 - (100 / (1 + gain / loss))

    # 변동률: 1h(4캔들), 4h(16캔들), 24h(96캔들)
    chg_1h = np.zeros(n)
    chg_4h = np.zeros(n)
    chg_24h = np.zeros(n)
    for offset, arr in [(4, chg_1h), (16, chg_4h), (96, chg_24h)]:
        for i in range(offset, n):
            arr[i] = (c[i] - c[i - offset]) / (c[i - offset] + 1e-9) * 100

    # BTC 7단계 레벨
    btc_level = np.zeros(n, dtype=int)
    for i in range(1, n):
        es, el = ema_s[i], ema_l[i]
        av = adx[i] if not np.isnan(adx[i]) else 0
        rs = rsi[i] if not np.isnan(rsi[i]) else 50
        ch1 = chg_1h[i]
        if es > el:
            if av > 30 and rs > 60 and ch1 > 1.0:
                btc_level[i] = 3
            elif av > 20:
                btc_level[i] = 2
            else:
                btc_level[i] = 1
        elif es < el:
            if av > 30 and rs < 40 and ch1 < -1.0:
                btc_level[i] = -3
            elif av > 20:
                btc_level[i] = -2
            else:
                btc_level[i] = -1
        else:
            btc_level[i] = 0

    result = pd.DataFrame({
        "Date": df["Date"],
        "btc_price": c,
        "btc_level": btc_level,
        "btc_atr_pct": np.round(btc_atr_pct, 4),
        "btc_rsi": np.round(rsi, 2),
        "btc_change_1h": np.round(chg_1h, 4),
        "btc_change_4h": np.round(chg_4h, 4),
        "btc_change_24h": np.round(chg_24h, 4),
    })
    result = result.set_index("Date").sort_index()

    nan_start = result["btc_atr_pct"].isna().sum()
    print(f"  BTC 피처 계산 완료: {len(result):,}행 "
          f"({result.index.min()} ~ {result.index.max()}, "
          f"초반 NaN {nan_start}행)")
    return result


# ═══════════════════════════════════════════════════════════════
#  시그널 라벨링 (TP/SL/Timeout 시뮬레이션)
# ═══════════════════════════════════════════════════════════════

LEVERAGE = 20
MAX_HOLD_CANDLES = 96


def _label_single_signal(signal: dict, ohlcv_df: pd.DataFrame) -> dict | None:
    """
    단일 시그널에 대해 TP/SL/Timeout 결과를 시뮬레이션.
    ohlcv_df: 해당 코인의 15분봉 (Date 인덱스, 정렬됨)
    """
    entry_time = signal["entry_time"]
    entry_price = signal["entry_price"]
    sl = signal["stop_loss"]
    tp = signal["take_profit"]
    direction = signal["direction"]

    mask = ohlcv_df.index > entry_time
    future = ohlcv_df.loc[mask]

    if len(future) == 0:
        return None

    future = future.iloc[:MAX_HOLD_CANDLES]

    mae = 0.0  # Maximum Adverse Excursion
    mfe = 0.0  # Maximum Favorable Excursion

    for i, (ts, row) in enumerate(future.iterrows()):
        h_val = row["High"]
        l_val = row["Low"]
        c_val = row["Close"]

        if direction == "long":
            pnl_high = (h_val - entry_price) / entry_price * 100
            pnl_low = (l_val - entry_price) / entry_price * 100
            mfe = max(mfe, pnl_high)
            mae = min(mae, pnl_low)

            if l_val <= sl:
                roe = (sl - entry_price) / entry_price * 100 * LEVERAGE
                return _build_label(signal, "SL", ts, sl, roe, i + 1,
                                    mae, mfe, pnl_low < 0 and pnl_high > 0)
            if h_val >= tp:
                roe = (tp - entry_price) / entry_price * 100 * LEVERAGE
                return _build_label(signal, "TP", ts, tp, roe, i + 1,
                                    mae, mfe, True)
        else:
            pnl_high = (entry_price - l_val) / entry_price * 100
            pnl_low = (entry_price - h_val) / entry_price * 100
            mfe = max(mfe, pnl_high)
            mae = min(mae, pnl_low)

            if h_val >= sl:
                roe = (entry_price - sl) / entry_price * 100 * LEVERAGE
                return _build_label(signal, "SL", ts, sl, roe, i + 1,
                                    mae, mfe, pnl_low < 0 and pnl_high > 0)
            if l_val <= tp:
                roe = (entry_price - tp) / entry_price * 100 * LEVERAGE
                return _build_label(signal, "TP", ts, tp, roe, i + 1,
                                    mae, mfe, True)

    # Timeout
    last_close = future.iloc[-1]["Close"]
    if direction == "long":
        roe = (last_close - entry_price) / entry_price * 100 * LEVERAGE
        dc = last_close > entry_price
    else:
        roe = (entry_price - last_close) / entry_price * 100 * LEVERAGE
        dc = last_close < entry_price

    return _build_label(signal, "Timeout", future.index[-1], last_close,
                        roe, len(future), mae, mfe, dc)


def _build_label(signal: dict, exit_type: str, exit_time, exit_price: float,
                 roe: float, hold: int, mae: float, mfe: float,
                 direction_correct: bool) -> dict:
    return {
        **signal,
        "exit_type": exit_type,
        "exit_time": str(exit_time),
        "exit_price": round(exit_price, 8),
        "realized_roe": round(roe, 4),
        "hold_candles": hold,
        "mae_pct": round(mae, 4),
        "mfe_pct": round(mfe, 4),
        "direction_correct": direction_correct,
    }


def _label_chunk(args):
    """멀티프로세싱 워커: 코인별 시그널 청크 라벨링"""
    chunk, ohlcv_dir = args
    results = []
    ohlcv_cache = {}

    for signal in chunk:
        sym = signal.get("symbol", "")
        if sym not in ohlcv_cache:
            fname = f"{sym}.csv"
            path = os.path.join(ohlcv_dir, fname)
            if os.path.exists(path):
                df = pd.read_csv(path)
                df["Date"] = pd.to_datetime(df["Date"])
                df = df.set_index("Date").sort_index()
                ohlcv_cache[sym] = df
            else:
                ohlcv_cache[sym] = None

        ohlcv_df = ohlcv_cache.get(sym)
        if ohlcv_df is None:
            continue

        labeled = _label_single_signal(signal, ohlcv_df)
        if labeled is not None:
            results.append(labeled)

    return results


# ═══════════════════════════════════════════════════════════════
#  컬럼 정규화 (cloud_scan_v2 → walk_forward 기대 형식)
# ═══════════════════════════════════════════════════════════════

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """cloud_scan_v2 컬럼명 → walk_forward.py가 기대하는 컬럼명으로 통일"""
    renames = {}

    # entry_time → timestamp
    if "entry_time" in df.columns and "timestamp" not in df.columns:
        renames["entry_time"] = "timestamp"

    # adx_value → adx
    if "adx_value" in df.columns and "adx" not in df.columns:
        renames["adx_value"] = "adx"

    # gap_pct → gap
    if "gap_pct" in df.columns and "gap" not in df.columns:
        renames["gap_pct"] = "gap"

    # btc7 → btc_level (cloud_scan_v2의 btc7은 이미 있을 수 있음, BTC 피처로 덮어씀)
    if "btc7" in df.columns and "btc_level" not in df.columns:
        renames["btc7"] = "btc_level"

    if renames:
        df = df.rename(columns=renames)
        print(f"  컬럼 정규화: {renames}")

    return df


# ═══════════════════════════════════════════════════════════════
#  BTC 피처 병합
# ═══════════════════════════════════════════════════════════════

def merge_btc_features(signals_df: pd.DataFrame,
                       btc_features: pd.DataFrame) -> pd.DataFrame:
    """
    시그널 timestamp 기준으로 BTC 피처를 asof merge.
    각 시그널 시점의 가장 가까운 이전 BTC 15분봉 데이터를 매칭.
    """
    signals_df = signals_df.copy()
    signals_df["timestamp"] = pd.to_datetime(signals_df["timestamp"])
    signals_df = signals_df.sort_values("timestamp").reset_index(drop=True)

    btc_reset = btc_features.reset_index().rename(columns={"Date": "timestamp"})
    btc_reset["timestamp"] = pd.to_datetime(btc_reset["timestamp"])

    merged = pd.merge_asof(
        signals_df,
        btc_reset,
        on="timestamp",
        direction="backward",
        suffixes=("", "_btc"),
    )

    # btc_level 충돌 처리: BTC 피처 계산값 우선
    if "btc_level_btc" in merged.columns:
        merged["btc_level"] = merged["btc_level_btc"]
        merged = merged.drop(columns=["btc_level_btc"])

    btc_cols = ["btc_price", "btc_level", "btc_atr_pct", "btc_rsi",
                "btc_change_1h", "btc_change_4h", "btc_change_24h"]
    for col in btc_cols:
        if col in merged.columns:
            merged[col] = merged[col].fillna(0)

    return merged


# ═══════════════════════════════════════════════════════════════
#  메인 빌더
# ═══════════════════════════════════════════════════════════════

def build_labeled_csv(
    signals_path: str,
    btc_path: str,
    ohlcv_dir: str,
    output_path: str,
    workers: int = 24,
    skip_labeling: bool = False,
):
    """
    전체 파이프라인 실행:
    1. 시그널 CSV 로드 + 컬럼 정규화
    2. BTC 15분봉에서 시장 상태 피처 계산
    3. 코인별 OHLCV로 시그널 라벨링 (TP/SL/Timeout)  [skip_labeling=True면 건너뜀]
    4. BTC 피처 병합
    5. 최종 CSV 저장
    """
    t0 = time.time()

    # ── 1. 시그널 로드 ──
    print(f"\n{'='*70}")
    print(f"  1단계: 시그널 로드")
    print(f"{'='*70}")
    signals_df = pd.read_csv(signals_path)
    print(f"  {len(signals_df):,}행 로드")
    print(f"  원본 컬럼: {list(signals_df.columns)}")

    signals_df = normalize_columns(signals_df)

    # ── 2. BTC 피처 계산 ──
    print(f"\n{'='*70}")
    print(f"  2단계: BTC 시장 상태 계산")
    print(f"{'='*70}")
    btc_features = compute_btc_features(btc_path)

    # ── 3. 라벨링 (TP/SL/Timeout) ──
    if skip_labeling:
        print(f"\n{'='*70}")
        print(f"  3단계: 라벨링 건너뜀 (이미 라벨링된 데이터)")
        print(f"{'='*70}")

        required_label_cols = ["exit_type", "realized_roe", "hold_candles",
                               "direction_correct"]
        missing = [c for c in required_label_cols if c not in signals_df.columns]
        if missing:
            print(f"  ⚠ 경고: 누락 컬럼 {missing} — 라벨링이 필요할 수 있습니다")
            print(f"  --skip-labeling 제거 후 재실행하세요")
            return

        labeled_df = signals_df
    else:
        print(f"\n{'='*70}")
        print(f"  3단계: 시그널 라벨링 (TP/SL/Timeout)")
        print(f"{'='*70}")

        # entry_time이 timestamp으로 바뀌었다면 복원
        records = signals_df.to_dict("records")
        for r in records:
            if "entry_time" not in r and "timestamp" in r:
                r["entry_time"] = r["timestamp"]
            r["entry_time"] = pd.Timestamp(r["entry_time"])

        # 코인별 그룹핑 → 청크 분할
        from collections import defaultdict
        by_symbol = defaultdict(list)
        for r in records:
            by_symbol[r.get("symbol", "UNKNOWN")].append(r)

        chunks = list(by_symbol.values())
        print(f"  {len(records):,} 시그널 / {len(chunks)} 코인 / {workers} 워커")

        t_label = time.time()
        if workers > 1 and len(chunks) > 1:
            import __main__
            if not hasattr(__main__, "__spec__"):
                __main__.__spec__ = None
            with mp.Pool(processes=min(workers, len(chunks))) as pool:
                results = pool.map(
                    _label_chunk,
                    [(chunk, ohlcv_dir) for chunk in chunks],
                )
            labeled = []
            for r in results:
                labeled.extend(r)
        else:
            labeled = []
            for chunk in chunks:
                labeled.extend(_label_chunk((chunk, ohlcv_dir)))

        elapsed_label = time.time() - t_label
        print(f"  라벨링 완료: {len(labeled):,}건 / {elapsed_label:.1f}초 "
              f"({len(labeled)/max(elapsed_label, 0.001):,.0f} 시그널/초)")

        if not labeled:
            print("  ❌ 라벨링 결과 0건 — OHLCV 경로 확인 필요")
            return

        labeled_df = pd.DataFrame(labeled)
        labeled_df = normalize_columns(labeled_df)

    # ── 4. BTC 피처 병합 ──
    print(f"\n{'='*70}")
    print(f"  4단계: BTC 시장 상태 병합")
    print(f"{'='*70}")
    final_df = merge_btc_features(labeled_df, btc_features)
    print(f"  병합 완료: {len(final_df):,}행")

    # ── 5. 저장 ──
    print(f"\n{'='*70}")
    print(f"  5단계: 저장")
    print(f"{'='*70}")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    final_df.to_csv(output_path, index=False)

    elapsed = time.time() - t0
    print(f"\n  최종 컬럼: {list(final_df.columns)}")
    print(f"  출력: {output_path}")
    print(f"  행 수: {len(final_df):,}")
    print(f"  파일 크기: {os.path.getsize(output_path)/1024/1024:.1f} MB")
    print(f"  소요 시간: {elapsed/60:.1f}분")

    # ── 검증 ──
    print(f"\n{'='*70}")
    print(f"  검증")
    print(f"{'='*70}")

    required = ["timestamp", "symbol", "direction", "confidence", "adx", "gap",
                 "entry_price", "stop_loss", "take_profit",
                 "exit_type", "realized_roe", "hold_candles", "direction_correct",
                 "btc_price", "btc_level", "btc_atr_pct", "btc_rsi",
                 "btc_change_24h"]
    present = [c for c in required if c in final_df.columns]
    missing = [c for c in required if c not in final_df.columns]

    print(f"  필수 컬럼 ({len(present)}/{len(required)}): ✅ {present}")
    if missing:
        print(f"  ⚠ 누락: {missing}")
    else:
        print(f"  ✅ walk_forward.py 호환 완료")

    # 기초 통계
    if "exit_type" in final_df.columns:
        print(f"\n  exit_type 분포:")
        for et, cnt in final_df["exit_type"].value_counts().items():
            pct = cnt / len(final_df) * 100
            print(f"    {et}: {cnt:,}건 ({pct:.1f}%)")

    if "realized_roe" in final_df.columns:
        print(f"\n  ROE 통계:")
        print(f"    평균: {final_df['realized_roe'].mean():.2f}%")
        print(f"    중앙값: {final_df['realized_roe'].median():.2f}%")
        print(f"    총합: {final_df['realized_roe'].sum():,.1f}%")

    if "btc_level" in final_df.columns:
        print(f"\n  BTC 레벨 분포:")
        for lv in sorted(final_df["btc_level"].unique()):
            cnt = len(final_df[final_df["btc_level"] == lv])
            print(f"    Level {lv:+d}: {cnt:,}건")

    print(f"\n{'='*70}")
    print(f"  완료! 백테스트 실행:")
    print(f"  python backtests/walk_forward.py --data \"{output_path}\"")
    print(f"{'='*70}")

    return final_df


# ═══════════════════════════════════════════════════════════════
#  CLI 진입점
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="백테스트용 라벨링 CSV 생성기",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:

  # 전체 파이프라인 (시그널 + 라벨링 + BTC 피처)
  python backtests/build_labeled_csv.py ^
      --signals D:/AutoTrade/Results/cloud_scan_v2/all/signals_all.csv ^
      --btc D:/AutoTrade/Raw_Data/CRYPTO_BINANCE_15M/BTC_USDT.csv ^
      --ohlcv D:/AutoTrade/Raw_Data/CRYPTO_BINANCE_15M/ ^
      --output D:/AutoTrade/Raw_Data/labeled_signals/signals_all_labeled.csv

  # 이미 라벨링된 CSV에 BTC 피처만 추가
  python backtests/build_labeled_csv.py ^
      --signals D:/AutoTrade/Raw_Data/labeled_signals/signals_all_labeled.csv ^
      --btc D:/AutoTrade/Raw_Data/CRYPTO_BINANCE_15M/BTC_USDT.csv ^
      --output D:/AutoTrade/Raw_Data/labeled_signals/signals_enriched.csv ^
      --skip-labeling
        """,
    )

    parser.add_argument("--signals", required=True,
                        help="시그널 CSV 경로 (cloud_scan_v2 출력 또는 기존 라벨링 CSV)")
    parser.add_argument("--btc", required=True,
                        help="BTC_USDT 15분봉 CSV 경로")
    parser.add_argument("--ohlcv", default="",
                        help="코인별 OHLCV 디렉토리 (라벨링에 필요, --skip-labeling 시 불필요)")
    parser.add_argument("--output", required=True,
                        help="출력 CSV 경로")
    parser.add_argument("--workers", type=int, default=min(24, mp.cpu_count() or 1),
                        help=f"멀티프로세싱 워커 수 (기본: {min(24, mp.cpu_count() or 1)})")
    parser.add_argument("--skip-labeling", action="store_true",
                        help="라벨링 건너뛰기 (이미 exit_type/realized_roe 있는 경우)")

    args = parser.parse_args()

    if not args.skip_labeling and not args.ohlcv:
        parser.error("--ohlcv 경로 필요 (또는 --skip-labeling 사용)")

    build_labeled_csv(
        signals_path=args.signals,
        btc_path=args.btc,
        ohlcv_dir=args.ohlcv,
        output_path=args.output,
        workers=args.workers,
        skip_labeling=args.skip_labeling,
    )


if __name__ == "__main__":
    main()
