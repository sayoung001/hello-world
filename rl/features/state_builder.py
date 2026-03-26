"""
State Builder — 시그널 CSV → RL 관측 벡터 변환 (고속 버전)
==========================================================
벡터화 + 멀티프로세싱으로 370만 시그널을 빠르게 처리.

- 관측 벡터: pandas 벡터 연산으로 일괄 생성 (iterrows 제거)
- OHLC 슬라이스: 코인별 병렬 처리 (multiprocessing)
"""

from __future__ import annotations

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TIMEOUT_CANDLES = 96

FEATURE_NAMES = [
    "atr_norm", "squeeze_range_norm", "gap_pct", "confidence",
    "adx_value", "volume_ratio", "rsi", "bbw", "direction",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
]


def _load_ohlcv(ohlcv_dir: Path, symbol: str) -> pd.DataFrame | None:
    """코인별 OHLCV 로드. 타임스탬프/OHLC 컬럼 자동 감지."""
    for name in [symbol, symbol.upper(), symbol.lower()]:
        csv_path = ohlcv_dir / f"{name}.csv"
        if csv_path.exists():
            break
    else:
        return None

    coin_df = pd.read_csv(csv_path)

    # 타임스탬프 컬럼 감지
    time_col = None
    for col_name in ["timestamp", "time", "date", "datetime"]:
        if col_name in coin_df.columns:
            time_col = col_name
            break
    if time_col is None:
        time_col = coin_df.columns[0]

    coin_df[time_col] = pd.to_datetime(coin_df[time_col])
    coin_df = coin_df.rename(columns={time_col: "ts"})
    coin_df = coin_df.sort_values("ts").reset_index(drop=True)

    # OHLC 컬럼 감지
    renames = {}
    for target, candidates in [
        ("open", ["open", "Open", "OPEN"]),
        ("high", ["high", "High", "HIGH"]),
        ("low", ["low", "Low", "LOW"]),
        ("close", ["close", "Close", "CLOSE"]),
    ]:
        for c in candidates:
            if c in coin_df.columns:
                renames[c] = target
                break
    if len(renames) < 4:
        return None

    coin_df = coin_df.rename(columns=renames)
    return coin_df


def _extract_slices_for_symbol(
    symbol: str,
    signal_times: list,
    signal_indices: list,
    ohlcv_dir: str,
) -> list[tuple[int, np.ndarray | None]]:
    """
    단일 코인의 모든 시그널에 대해 OHLC 슬라이스 추출.
    멀티프로세싱 워커 함수.

    Returns: [(원본_인덱스, ohlc_slice 또는 None), ...]
    """
    coin_df = _load_ohlcv(Path(ohlcv_dir), symbol)
    if coin_df is None:
        return [(idx, None) for idx in signal_indices]

    # 타임스탬프 → 인덱스 매핑 (빠른 검색용)
    ts_to_idx = pd.Series(coin_df.index, index=coin_df["ts"])
    ohlc_values = coin_df[["open", "high", "low", "close"]].values.astype(np.float32)
    n_rows = len(coin_df)

    results = []
    for orig_idx, ts in zip(signal_indices, signal_times):
        ts = pd.Timestamp(ts)

        # 정확 매칭
        if ts in ts_to_idx.index:
            entry_idx = ts_to_idx[ts]
            if isinstance(entry_idx, pd.Series):
                entry_idx = entry_idx.iloc[0]
        else:
            # 가장 가까운 캔들 (15분 이내)
            time_diff = (coin_df["ts"] - ts).abs()
            closest_idx = time_diff.idxmin()
            if time_diff.iloc[closest_idx] > pd.Timedelta(minutes=15):
                results.append((orig_idx, None))
                continue
            entry_idx = closest_idx

        # OHLC 슬라이스
        start = entry_idx + 1
        end = min(start + TIMEOUT_CANDLES, n_rows)
        if start >= n_rows:
            results.append((orig_idx, None))
            continue

        ohlc_slice = ohlc_values[start:end]
        results.append((orig_idx, ohlc_slice))

    return results


def build_observations(
    signals_df: pd.DataFrame,
    ohlcv_dir: str | Path,
    max_signals: int | None = None,
    n_workers: int = 8,
) -> dict[str, np.ndarray | list]:
    """
    시그널 DataFrame → RL 환경 입력 데이터 생성 (고속 버전).

    1단계: 관측 벡터를 벡터 연산으로 일괄 생성 (수 초)
    2단계: OHLC 슬라이스를 코인별 병렬 추출 (멀티프로세싱)
    """
    ohlcv_dir = Path(ohlcv_dir)
    df = signals_df.copy()

    if max_signals is not None:
        df = df.head(max_signals)

    # 방향 숫자화
    df["dir_num"] = df["direction"].str.lower().map({"long": 1.0, "short": -1.0})
    df = df.dropna(subset=["dir_num"])
    df["entry_time"] = pd.to_datetime(df["entry_time"])

    # SL/TP 거리
    df["sl_dist"] = (df["entry_price"] - df["stop_loss"]).abs()
    df["tp_dist"] = (df["take_profit"] - df["entry_price"]).abs()

    # 유효하지 않은 행 제거
    df = df[(df["sl_dist"] > 1e-8) & (df["tp_dist"] > 1e-8)].reset_index(drop=True)
    logger.info(f"유효 시그널: {len(df)}건")

    # ── 1단계: 관측 벡터 벡터화 생성 ──────────────────────────
    logger.info("관측 벡터 벡터화 생성 중...")
    entry_prices = df["entry_price"].values
    ep_safe = np.maximum(entry_prices, 1e-8)

    hour = df["entry_time"].dt.hour + df["entry_time"].dt.minute / 60.0
    dow = df["entry_time"].dt.weekday

    obs_array = np.column_stack([
        df["atr"].fillna(0).values / ep_safe,                                    # atr_norm
        df["squeeze_range"].fillna(0).values / ep_safe,                           # squeeze_range_norm
        df["gap_pct"].fillna(0).values,                                           # gap_pct
        df["confidence"].fillna(50).values / 100.0,                               # confidence
        df["adx_value"].fillna(25).values / 100.0,                                # adx_value
        np.clip(df["volume_ratio"].fillna(1).values, 0, 10) / 10.0,              # volume_ratio
        df["rsi"].fillna(50).values / 100.0,                                      # rsi
        np.clip(df["bbw"].fillna(0).values, 0, 0.5) / 0.5,                       # bbw
        df["dir_num"].values,                                                      # direction
        np.sin(2 * np.pi * hour / 24),                                            # hour_sin
        np.cos(2 * np.pi * hour / 24),                                            # hour_cos
        np.sin(2 * np.pi * dow / 7),                                              # dow_sin
        np.cos(2 * np.pi * dow / 7),                                              # dow_cos
    ]).astype(np.float32)

    logger.info(f"관측 벡터 생성 완료: {obs_array.shape}")

    # ── 2단계: OHLC 슬라이스 병렬 추출 ──────────────────────
    logger.info(f"OHLC 슬라이스 추출 중 ({n_workers} 워커)...")

    # 코인별 시그널 그룹핑
    symbol_groups: dict[str, tuple[list, list]] = {}
    for i, row in df.iterrows():
        sym = row["symbol"]
        if sym not in symbol_groups:
            symbol_groups[sym] = ([], [])
        symbol_groups[sym][0].append(row["entry_time"])
        symbol_groups[sym][1].append(i)

    logger.info(f"  {len(symbol_groups)}개 코인 처리")

    # 병렬 실행
    ohlc_results: dict[int, np.ndarray | None] = {}

    with ProcessPoolExecutor(max_workers=n_workers) as executor:
        futures = {}
        for sym, (times, indices) in symbol_groups.items():
            f = executor.submit(
                _extract_slices_for_symbol,
                sym, times, indices, str(ohlcv_dir),
            )
            futures[f] = sym

        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            if done_count % 20 == 0:
                logger.info(f"  코인 처리: {done_count}/{len(symbol_groups)}")
            for orig_idx, ohlc_slice in future.result():
                ohlc_results[orig_idx] = ohlc_slice

    # ── 3단계: 유효한 것만 필터링 ────────────────────────────
    valid_mask = []
    ohlc_slices = []
    for i in range(len(df)):
        ohlc = ohlc_results.get(i)
        if ohlc is not None and len(ohlc) > 0:
            valid_mask.append(i)
            ohlc_slices.append(ohlc)

    valid_mask = np.array(valid_mask)
    skipped = len(df) - len(valid_mask)
    if skipped > 0:
        logger.warning(f"{skipped}건 스킵 (OHLCV 미매칭 또는 데이터 부족)")

    logger.info(f"최종 관측 벡터 {len(valid_mask)}건 완료")

    return {
        "observations": obs_array[valid_mask],
        "ohlc_slices": ohlc_slices,
        "base_sl_distances": df["sl_dist"].values[valid_mask].astype(np.float32),
        "tp_distances": df["tp_dist"].values[valid_mask].astype(np.float32),
        "entry_prices": entry_prices[valid_mask].astype(np.float32),
        "directions": df["dir_num"].values[valid_mask].astype(np.float32),
    }


def build_from_csv(
    signals_csv: str | Path,
    ohlcv_dir: str | Path,
    max_signals: int | None = None,
    n_workers: int = 8,
) -> dict[str, np.ndarray | list]:
    """CSV 파일 경로에서 직접 로드하여 build_observations 호출."""
    df = pd.read_csv(signals_csv)
    return build_observations(df, ohlcv_dir, max_signals=max_signals, n_workers=n_workers)
