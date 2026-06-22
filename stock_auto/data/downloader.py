"""
일봉 OHLCV 다운로더 (코인봇 download_data_v2.py 계승 → 주식 전환).

- 1차: FinanceDataReader (한·미 광범위 무료)
- 2차: yfinance (미국 보조)
- CSV 캐시: 재다운로드 방지
- 합성데이터 생성기: 네트워크 불가 환경에서 로직 검증용

표준 출력 컬럼: Open High Low Close Volume  (+ Turnover 거래대금)
거래대금(Turnover)은 시장 통화 기준 = Close × Volume 근사(분석 §3.1 통화 일관성).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from stock_auto.config.settings import Market

CACHE_DIR = Path(os.environ.get("STOCK_DATA_DIR", "data/ohlcv_cache"))

STD_COLS = ["Open", "High", "Low", "Close", "Volume"]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """컬럼명 표준화 + 거래대금 컬럼 보강."""
    rename = {c: c.capitalize() for c in df.columns}
    df = df.rename(columns=rename)
    # yfinance 'Adj close' 등 대응
    if "Adj close" in df.columns and "Close" not in df.columns:
        df["Close"] = df["Adj close"]
    missing = [c for c in STD_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing} (가진 컬럼 {list(df.columns)})")
    df = df[STD_COLS].copy()
    df = df.dropna()
    # 거래대금(통화 기준) — 시장 통화 일관성 유지
    df["Turnover"] = df["Close"] * df["Volume"]
    return df


def _cache_path(symbol: str, market: Market) -> Path:
    return CACHE_DIR / market.value / f"{symbol}.csv"


def download_ohlcv(
    symbol: str,
    start: str,
    end: Optional[str] = None,
    market: Market = Market.US,
) -> pd.DataFrame:
    """FDR → yfinance 순으로 일봉을 받아 표준 형식으로 반환."""
    last_err: Optional[Exception] = None
    # 1차 FDR
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(symbol, start, end)
        if df is not None and len(df) > 0:
            return _normalize(df)
    except Exception as e:  # noqa: BLE001
        last_err = e
    # 2차 yfinance (미국)
    if market == Market.US:
        try:
            import yfinance as yf
            df = yf.download(symbol, start=start, end=end, progress=False,
                             auto_adjust=True)
            if df is not None and len(df) > 0:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                return _normalize(df)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"{symbol} 다운로드 실패: {last_err}")


def load_or_download(
    symbol: str,
    start: str,
    end: Optional[str] = None,
    market: Market = Market.US,
    use_cache: bool = True,
) -> pd.DataFrame:
    """CSV 캐시 우선 로드, 없으면 다운로드 후 저장."""
    path = _cache_path(symbol, market)
    if use_cache and path.exists():
        return pd.read_csv(path, index_col=0, parse_dates=True)
    df = download_ohlcv(symbol, start, end, market)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return df


def make_synthetic(
    n: int = 200,
    seed: int = 0,
    market: Market = Market.US,
    trend: float = 0.0005,
    base_price: float = 100.0,
    base_volume: float = 1_000_000.0,
) -> pd.DataFrame:
    """
    오프라인 로직 검증용 합성 일봉 생성.
    GBM 가격 + 로그정규 거래량. market에 따라 통화 스케일을 다르게 줘
    통화버그 수정을 검증할 수 있게 한다(KR은 원화라 거래대금이 훨씬 큼).
    """
    rng = np.random.default_rng(seed)
    rets = rng.normal(trend, 0.02, n)
    close = base_price * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    # KR은 가격대(원)·거래량 스케일이 커서 거래대금이 수십~수백억 → 통화 분기 검증
    vol_scale = base_volume * (1.0 if market == Market.US else 50.0)
    volume = rng.lognormal(np.log(vol_scale), 0.5, n)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    df = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=idx,
    )
    df["Turnover"] = df["Close"] * df["Volume"]
    return df
