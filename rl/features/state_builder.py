"""
State Builder — 시그널 CSV → RL 관측 벡터 변환
==============================================
signals_all_labeled.csv에서 RL 환경에 필요한 데이터를 추출·정규화.

CSV 컬럼 (실제):
    entry_time, entry_price, stop_loss, take_profit, direction,
    confidence, atr, adx_value, rsi, volume_ratio, gap_pct,
    squeeze_candles, squeeze_range, squeeze_high, squeeze_low,
    body_ratio, wick_ratio, bbw, bbw_avg, vol_decrease,
    candle_idx, symbol, btc7, data_tier, data_days,
    exit_type, exit_time, exit_price, realized_roe,
    hold_candles, mae_pct, mfe_pct, direction_correct

입력: 라벨링된 시그널 CSV + 코인별 OHLCV 15분봉
출력: (observations, ohlc_slices, base_sl_distances, tp_distances, entry_prices, directions)
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 타임아웃 캔들 수
TIMEOUT_CANDLES = 96

# 관측 피처 이름 (OBS_DIM=13과 일치해야 함)
FEATURE_NAMES = [
    "atr_norm",
    "squeeze_range_norm",
    "gap_pct",
    "confidence",
    "adx_value",
    "volume_ratio",
    "rsi",
    "bbw",
    "direction",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
]


def build_observations(
    signals_df: pd.DataFrame,
    ohlcv_dir: str | Path,
    max_signals: int | None = None,
) -> dict[str, np.ndarray | list]:
    """
    시그널 DataFrame + OHLCV 디렉토리 → RL 환경 입력 데이터 생성.

    CSV에 atr, rsi, volume_ratio, bbw 등 지표가 이미 포함되어 있으므로
    OHLCV는 진입 이후 가격 시뮬레이션(ohlc_slices)에만 사용.

    Parameters
    ----------
    signals_df : pd.DataFrame
        outcome_labeler.py 출력 (33컬럼).
    ohlcv_dir : str | Path
        코인별 15분봉 CSV 디렉토리 (예: Raw_Data/CRYPTO_BINANCE_15M/)
    max_signals : int | None
        디버깅용 — 처리할 최대 시그널 수

    Returns
    -------
    dict with keys:
        observations: np.ndarray (N, 13)
        ohlc_slices: list[np.ndarray] — 각 (<=96, 4)
        base_sl_distances: np.ndarray (N,)
        tp_distances: np.ndarray (N,)
        entry_prices: np.ndarray (N,)
        directions: np.ndarray (N,)  — 1=LONG, -1=SHORT
    """
    ohlcv_dir = Path(ohlcv_dir)
    df = signals_df.copy()

    if max_signals is not None:
        df = df.head(max_signals)

    # 방향 숫자화 (소문자 "long"/"short" 대응)
    df["dir_num"] = df["direction"].str.lower().map({"long": 1.0, "short": -1.0})
    df = df.dropna(subset=["dir_num"])

    # 타임스탬프 파싱
    df["entry_time"] = pd.to_datetime(df["entry_time"])

    observations = []
    ohlc_slices = []
    base_sl_distances = []
    tp_distances = []
    entry_prices = []
    directions = []

    # 코인별 OHLCV 캐시
    ohlcv_cache: dict[str, pd.DataFrame] = {}

    skipped = 0
    total = len(df)

    for i, (_, row) in enumerate(df.iterrows()):
        if i % 500 == 0:
            logger.info(f"  처리 중: {i}/{total}")

        symbol = row["symbol"]

        # OHLCV 로드 (캐시)
        if symbol not in ohlcv_cache:
            # 파일명 패턴 시도: symbol.csv 또는 SYMBOL.csv
            csv_path = ohlcv_dir / f"{symbol}.csv"
            if not csv_path.exists():
                csv_path = ohlcv_dir / f"{symbol.upper()}.csv"
            if not csv_path.exists():
                csv_path = ohlcv_dir / f"{symbol.lower()}.csv"
            if not csv_path.exists():
                skipped += 1
                ohlcv_cache[symbol] = None
                continue
            coin_df = pd.read_csv(csv_path)
            # 타임스탬프 컬럼 자동 감지
            time_col = None
            for col_name in ["timestamp", "time", "date", "datetime", "entry_time"]:
                if col_name in coin_df.columns:
                    time_col = col_name
                    break
            if time_col is None:
                # 첫 번째 컬럼이 시간일 가능성
                time_col = coin_df.columns[0]
            coin_df[time_col] = pd.to_datetime(coin_df[time_col])
            coin_df = coin_df.rename(columns={time_col: "ts"})
            coin_df = coin_df.sort_values("ts").reset_index(drop=True)

            # OHLC 컬럼 자동 감지
            ohlc_cols = {}
            for target, candidates in [
                ("open", ["open", "Open", "OPEN"]),
                ("high", ["high", "High", "HIGH"]),
                ("low", ["low", "Low", "LOW"]),
                ("close", ["close", "Close", "CLOSE"]),
            ]:
                for c in candidates:
                    if c in coin_df.columns:
                        ohlc_cols[target] = c
                        break

            if len(ohlc_cols) < 4:
                skipped += 1
                ohlcv_cache[symbol] = None
                continue

            coin_df = coin_df.rename(columns={v: k for k, v in ohlc_cols.items()})
            ohlcv_cache[symbol] = coin_df

        coin_df = ohlcv_cache[symbol]
        if coin_df is None:
            skipped += 1
            continue

        # 진입 캔들 찾기 (타임스탬프 매칭)
        ts = row["entry_time"]
        mask = coin_df["ts"] == ts
        if not mask.any():
            # 가장 가까운 캔들 찾기 (15분 이내)
            time_diff = (coin_df["ts"] - ts).abs()
            closest_idx = time_diff.idxmin()
            if time_diff.loc[closest_idx] > pd.Timedelta(minutes=15):
                skipped += 1
                continue
            entry_idx = closest_idx
        else:
            entry_idx = mask.idxmax()

        # 진입 이후 OHLC 슬라이스 (최대 TIMEOUT_CANDLES)
        end_idx = min(entry_idx + 1 + TIMEOUT_CANDLES, len(coin_df))
        ohlc_slice = coin_df.iloc[entry_idx + 1 : end_idx][
            ["open", "high", "low", "close"]
        ].values.astype(np.float32)

        if len(ohlc_slice) == 0:
            skipped += 1
            continue

        # CSV에서 직접 값 가져오기
        entry_price = float(row["entry_price"])

        # SL/TP 거리
        sl_dist = abs(entry_price - float(row["stop_loss"]))
        tp_dist = abs(float(row["take_profit"]) - entry_price)
        if sl_dist < 1e-8 or tp_dist < 1e-8:
            skipped += 1
            continue

        # CSV에 이미 있는 지표 사용
        atr_val = float(row.get("atr", 0.0))
        rsi_val = float(row.get("rsi", 50.0))
        bbw_val = float(row.get("bbw", 0.0))
        vol_ratio = float(row.get("volume_ratio", 1.0))
        squeeze_range = float(row.get("squeeze_range", 0.0))
        gap_pct = float(row.get("gap_pct", 0.0))
        confidence = float(row.get("confidence", 50.0))
        adx_val = float(row.get("adx_value", 25.0))

        # 시간 사이클 인코딩
        hour = ts.hour + ts.minute / 60.0
        dow = ts.weekday()
        hour_sin = np.sin(2 * np.pi * hour / 24)
        hour_cos = np.cos(2 * np.pi * hour / 24)
        dow_sin = np.sin(2 * np.pi * dow / 7)
        dow_cos = np.cos(2 * np.pi * dow / 7)

        # 관측 벡터 조립 (13차원)
        obs = np.array(
            [
                atr_val / max(entry_price, 1e-8),           # atr_norm
                squeeze_range / max(entry_price, 1e-8),     # squeeze_range_norm
                gap_pct,                                     # gap_pct (이미 %)
                confidence / 100.0,                          # 0~1 정규화
                adx_val / 100.0,                             # 0~1 정규화
                np.clip(vol_ratio, 0.0, 10.0) / 10.0,       # 0~1 정규화
                rsi_val / 100.0,                             # 0~1 정규화
                np.clip(bbw_val, 0.0, 0.5) / 0.5,           # 0~1 정규화
                float(row["dir_num"]),                       # 1 or -1
                hour_sin,
                hour_cos,
                dow_sin,
                dow_cos,
            ],
            dtype=np.float32,
        )

        observations.append(obs)
        ohlc_slices.append(ohlc_slice)
        base_sl_distances.append(sl_dist)
        tp_distances.append(tp_dist)
        entry_prices.append(entry_price)
        directions.append(float(row["dir_num"]))

    if skipped > 0:
        logger.warning(f"{skipped}건 시그널 스킵 (OHLCV 미매칭 또는 데이터 부족)")

    logger.info(f"관측 벡터 {len(observations)}건 생성 완료")

    return {
        "observations": np.array(observations, dtype=np.float32) if observations else np.empty((0, 13), dtype=np.float32),
        "ohlc_slices": ohlc_slices,
        "base_sl_distances": np.array(base_sl_distances, dtype=np.float32),
        "tp_distances": np.array(tp_distances, dtype=np.float32),
        "entry_prices": np.array(entry_prices, dtype=np.float32),
        "directions": np.array(directions, dtype=np.float32),
    }


def build_from_csv(
    signals_csv: str | Path,
    ohlcv_dir: str | Path,
    max_signals: int | None = None,
) -> dict[str, np.ndarray | list]:
    """CSV 파일 경로에서 직접 로드하여 build_observations 호출."""
    df = pd.read_csv(signals_csv)
    return build_observations(df, ohlcv_dir, max_signals=max_signals)
