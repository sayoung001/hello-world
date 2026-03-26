"""
State Builder — 시그널 CSV → RL 관측 벡터 변환
==============================================
signals_all_labeled.csv에서 RL 환경에 필요한 데이터를 추출·정규화.

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
    "adx",
    "volume_ratio",
    "rsi",
    "bb_width",
    "direction",
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
]


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR 계산 (OHLC DataFrame 필요)."""
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI 계산."""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_bb_width(close: pd.Series, period: int = 20, std_mult: float = 2.0) -> pd.Series:
    """볼린저밴드 폭 / 가격 (정규화된 BB 폭)."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return (upper - lower) / mid


def build_observations(
    signals_df: pd.DataFrame,
    ohlcv_dir: str | Path,
    max_signals: int | None = None,
) -> dict[str, np.ndarray | list]:
    """
    시그널 DataFrame + OHLCV 디렉토리 → RL 환경 입력 데이터 생성.

    Parameters
    ----------
    signals_df : pd.DataFrame
        outcome_labeler.py 출력 (33컬럼).
        필수 컬럼: symbol, timestamp, direction, confidence, adx, gap_pct,
                   squeeze_low, squeeze_high, squeeze_range, entry_price,
                   sl_price, tp_price
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

    # 방향 숫자화
    df["dir_num"] = df["direction"].map({"LONG": 1.0, "SHORT": -1.0})
    df = df.dropna(subset=["dir_num"])

    # 타임스탬프 파싱
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    observations = []
    ohlc_slices = []
    base_sl_distances = []
    tp_distances = []
    entry_prices = []
    directions = []

    # 코인별 OHLCV 캐시
    ohlcv_cache: dict[str, pd.DataFrame] = {}

    skipped = 0
    for _, row in df.iterrows():
        symbol = row["symbol"]

        # OHLCV 로드 (캐시)
        if symbol not in ohlcv_cache:
            csv_path = ohlcv_dir / f"{symbol}.csv"
            if not csv_path.exists():
                skipped += 1
                continue
            coin_df = pd.read_csv(csv_path, parse_dates=["timestamp"])
            coin_df = coin_df.sort_values("timestamp").reset_index(drop=True)
            # 지표 미리 계산
            coin_df["atr"] = compute_atr(coin_df)
            coin_df["rsi"] = compute_rsi(coin_df["close"])
            coin_df["bb_width"] = compute_bb_width(coin_df["close"])
            coin_df["vol_ma20"] = coin_df["volume"].rolling(20).mean()
            ohlcv_cache[symbol] = coin_df

        coin_df = ohlcv_cache[symbol]

        # 진입 캔들 찾기 (타임스탬프 매칭)
        ts = row["timestamp"]
        mask = coin_df["timestamp"] == ts
        if not mask.any():
            # 가장 가까운 캔들 찾기 (15분 이내)
            time_diff = (coin_df["timestamp"] - ts).abs()
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

        # 진입 시점 지표
        entry_row = coin_df.iloc[entry_idx]
        entry_price = float(row["entry_price"])
        atr_val = float(entry_row.get("atr", 0.0))
        rsi_val = float(entry_row.get("rsi", 50.0))
        bb_w = float(entry_row.get("bb_width", 0.0))
        vol_ma = float(entry_row.get("vol_ma20", 1.0))
        vol_ratio = float(entry_row["volume"]) / max(vol_ma, 1e-8)

        # SL/TP 거리
        sl_dist = abs(entry_price - float(row["sl_price"]))
        tp_dist = abs(float(row["tp_price"]) - entry_price)
        if sl_dist < 1e-8 or tp_dist < 1e-8:
            skipped += 1
            continue

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
                atr_val / max(entry_price, 1e-8),  # atr_norm
                float(row.get("squeeze_range", 0.0)) / max(entry_price, 1e-8),  # squeeze_range_norm
                float(row.get("gap_pct", 0.0)),
                float(row.get("confidence", 50.0)) / 100.0,  # 0~1 정규화
                float(row.get("adx", 25.0)) / 100.0,  # 0~1 정규화
                np.clip(vol_ratio, 0.0, 10.0) / 10.0,  # 0~1 정규화
                rsi_val / 100.0,  # 0~1 정규화
                np.clip(bb_w, 0.0, 0.5) / 0.5,  # 0~1 정규화
                float(row["dir_num"]),  # 1 or -1
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
        "observations": np.array(observations, dtype=np.float32),
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
