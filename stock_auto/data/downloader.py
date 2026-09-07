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


def _meta_path(symbol: str, market: Market) -> Path:
    return CACHE_DIR / market.value / f"{symbol}.meta.json"


def download_ohlcv(
    symbol: str,
    start: str,
    end: Optional[str] = None,
    market: Market = Market.US,
) -> tuple[pd.DataFrame, str]:
    """
    일봉을 받아 (표준 DataFrame, 소스명)을 반환.

    ★ US는 yfinance(auto_adjust=True)를 1차로 쓴다.
      소스마다 수정주가 정책이 다르면 종목 간 점수 비교(횡단면 모멘텀·상대 순위)가
      근본적으로 어긋난다. A는 FDR, B는 yfinance 같은 혼합을 막기 위해
      **시장별로 우선순위를 고정**하고, 실제로 쓴 소스를 캐시에 기록한다.
    """
    last_err: Optional[Exception] = None
    order = (("yfinance", "fdr") if market == Market.US else ("fdr", "yfinance"))
    for src in order:
        try:
            if src == "fdr":
                import FinanceDataReader as fdr
                df = fdr.DataReader(symbol, start, end)
            else:
                import yfinance as yf
                df = yf.download(symbol, start=start, end=end, progress=False,
                                 auto_adjust=True)
                if df is not None and isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
            if df is not None and len(df) > 0:
                return _normalize(df), src
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"{symbol} 다운로드 실패: {last_err}")


def expected_last_bar(market: Market = Market.US,
                      end: Optional[str] = None) -> pd.Timestamp:
    """
    지금 시점에 존재해야 할 '가장 최근 일봉'의 날짜(거래소 현지 기준, 주말 보정).

    공휴일 달력은 반영하지 않는다. 공휴일에는 이 값이 실제 마지막 봉보다 하루 앞서
    캐시를 한 번 더 받게 되지만(불필요한 다운로드 1회), 낡은 데이터를 그대로 쓰는
    쪽보다 훨씬 안전하다.
    """
    from stock_auto.config.clock import market_now
    if end:
        ts = pd.Timestamp(end)
    else:
        now = market_now(market)
        ts = pd.Timestamp(now.date())
        # 장 마감 전이면 오늘 봉은 아직 없다 (US 16:00 / KR 15:30 현지)
        close_h, close_m = (16, 0) if market == Market.US else (15, 30)
        if (now.hour, now.minute) < (close_h, close_m):
            ts -= pd.Timedelta(days=1)
    while ts.weekday() >= 5:            # 토(5)·일(6) → 직전 금요일
        ts -= pd.Timedelta(days=1)
    return ts.normalize()


def load_or_download(
    symbol: str,
    start: str,
    end: Optional[str] = None,
    market: Market = Market.US,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    CSV 캐시 우선 로드. 단, 캐시가 낡았으면 갱신한다.

    ★ 캐시를 무조건 신뢰하면 매일 도는 배치가 첫날 데이터에 영원히 고정된다.
      (신호가 매일 동일 → 기록은 중복으로 걸러져 0건 적립 → 시스템이 멈춘 줄 모른다)
      따라서 '있어야 할 마지막 봉'과 캐시의 마지막 봉을 비교해 판단한다.

    ★ 수정주가 정합성: 액면분할·배당이 발생하면 조정계수가 바뀐다. 캐시 앞부분은
      옛 계수, 새로 받은 뒷부분은 새 계수가 되어 **시계열 중간에 인위적 가격 점프**가
      생기고 ATR·모멘텀·MA 배열·레짐이 전부 오염된다. 배치는 정상 종료하며 아무도 모른다.
      → 병합 전에 겹치는 날짜의 종가를 비교해 괴리가 크면 캐시를 통째로 버린다.
    """
    path = _cache_path(symbol, market)
    prev_src = _read_meta(symbol, market)
    cached: Optional[pd.DataFrame] = None
    if use_cache and path.exists():
        try:
            cached = pd.read_csv(path, index_col=0, parse_dates=True)
        except Exception:  # noqa: BLE001 — 손상 캐시는 버리고 새로 받는다
            cached = None
        if cached is not None and not cached.empty:
            fresh = pd.Timestamp(cached.index[-1]).normalize() >= \
                expected_last_bar(market, end)
            # 시작일은 정확히 일치할 수 없다(요청 시작일이 휴장일이면 첫 봉은 그 뒤).
            # 매일 start가 하루씩 밀리므로 여유(10일)를 두지 않으면 캐시가 무의미해진다.
            covers = pd.Timestamp(cached.index[0]).normalize() <= \
                pd.Timestamp(start).normalize() + pd.Timedelta(days=10)
            if fresh and covers:
                return cached

    df, src = download_ohlcv(symbol, start, end, market)
    if cached is not None and not cached.empty:
        if prev_src and prev_src != src:
            print(f"[data] {symbol} 소스 변경 {prev_src}→{src} — 캐시 폐기 후 재구축")
            cached = None
        elif _adjustment_changed(cached, df, symbol):
            cached = None
    if cached is not None and not cached.empty:
        # 과거 구간은 캐시를, 겹치는 날짜는 새로 받은 값을 우선한다
        df = pd.concat([cached, df])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    _write_meta(symbol, market, src, len(df),
                pd.Timestamp(df.index[-1]).strftime("%Y-%m-%d"))
    return df


# 겹치는 날짜의 종가가 이 비율 이상 다르면 조정계수가 바뀐 것으로 본다
ADJUST_TOLERANCE = 0.005      # 0.5%


def _adjustment_changed(cached: pd.DataFrame, fresh: pd.DataFrame,
                        symbol: str) -> bool:
    """겹치는 구간의 종가를 비교해 수정주가 계수 변경 여부를 판정."""
    common = cached.index.intersection(fresh.index)
    if len(common) < 5:
        return False          # 겹치는 구간이 없으면 판정 불가 → 병합 진행
    a = cached.loc[common, "Close"].astype(float)
    b = fresh.loc[common, "Close"].astype(float)
    denom = b.replace(0, pd.NA).abs()
    diff = ((a - b).abs() / denom).dropna()
    if diff.empty:
        return False
    worst = float(diff.max())
    if worst > ADJUST_TOLERANCE:
        print(f"[data] ⚠️ {symbol} 수정주가 불일치 최대 {worst * 100:.2f}% "
              f"(허용 {ADJUST_TOLERANCE * 100:.1f}%) — 분할/배당 조정으로 보고 캐시 폐기")
        return True
    return False


def _write_meta(symbol: str, market: Market, source: str,
                rows: int, last_bar: str) -> None:
    import json
    from stock_auto.config.clock import now_et
    p = _meta_path(symbol, market)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "symbol": symbol, "market": market.value, "source": source,
        "rows": rows, "last_bar": last_bar,
        "updated_at": now_et().strftime("%Y-%m-%d %H:%M %Z"),
    }, ensure_ascii=False), encoding="utf-8")


def _read_meta(symbol: str, market: Market) -> Optional[str]:
    """캐시를 만든 소스명. 없으면 None."""
    import json
    p = _meta_path(symbol, market)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("source")
    except Exception:  # noqa: BLE001
        return None


def cache_sources(market: Market = Market.US) -> dict[str, int]:
    """캐시된 종목들이 어느 소스에서 왔는지 집계 — 소스 혼합 점검용."""
    import json
    from collections import Counter
    c: Counter = Counter()
    base = CACHE_DIR / market.value
    if not base.exists():
        return {}
    for p in base.glob("*.meta.json"):
        try:
            c[json.loads(p.read_text(encoding="utf-8")).get("source", "?")] += 1
        except Exception:  # noqa: BLE001
            c["?"] += 1
    return dict(c)


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
