"""
지표 계산 (US_KR_)CLAUD indicators_v2.py 설계 계승 + ★통화버그 수정).

핵심:
  - VAI 2단: 거래량 가속 + 지속성. look-ahead 방지 위해 baseline은 shift(1).
  - Liquidity Score: sigmoid(거래대금) → 0.3~1.4, ★시장별 통화 center 적용.
  - Penalty: 윗꼬리 / 장중밀림 / 단발거래량 / 저유동성 차감.
  - 보조지표: EMA, RSI, ATR, ADX(간이), 상대거래량.

데이터 누출 방지(분석 §2.1): 거래량/변동성 baseline은 shift(1) 기준.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from stock_auto.config.settings import (
    Market, get_config, VAI_SUSTAIN, VAI_SPIKE,
)


# ── 기본 보조지표 ──────────────────────────────────────────────────────────
def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()],
                   axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """간이 ADX (추세 강도)."""
    h, l = df["High"], df["Low"]
    up, dn = h.diff(), -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr = _atr(df, period)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0)


# ── ★ 유동성 점수 (시장별 통화 — 버그 수정) ────────────────────────────────
def calc_liquidity_score(turnover: pd.Series, market: Market) -> pd.Series:
    """
    거래대금(시장 통화) → 0.3~1.4 연속 점수.
    분석 §3.1: 반드시 시장별 center를 적용해야 KR이 포화되지 않는다.
    score = 0.3 + 1.1 * sigmoid( (log(turnover) - log(center)) / scale )
    """
    cfg = get_config(market)
    tv = turnover.clip(lower=1.0)
    x = (np.log(tv) - np.log(cfg.liquidity_center)) / cfg.liquidity_scale
    sig = 1 / (1 + np.exp(-x))
    return 0.3 + 1.1 * sig


# ── VAI 2단 (거래량 가속 + 지속) ───────────────────────────────────────────
def calc_vai(volume: pd.Series, window: int = 20) -> pd.DataFrame:
    """
    VAI(Volume Acceleration Index) = 오늘 거래량 / 과거 평균(shift(1) baseline).
    - vai: 가속 비율
    - vai_sustain: 최근 3일 중 2일 이상 vai≥SUSTAIN → 지속성(단발 아님)
    - vai_spike: vai≥SPIKE & 지속 아님 → 단발 펌핑 의심
    """
    base = volume.shift(1).rolling(window).mean()
    vai = (volume / base).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    sustain_cnt = (vai >= VAI_SUSTAIN).rolling(3).sum()
    vai_sustain = sustain_cnt >= 2
    vai_spike = (vai >= VAI_SPIKE) & (~vai_sustain)
    return pd.DataFrame(
        {"vai": vai, "vai_sustain": vai_sustain, "vai_spike": vai_spike},
        index=volume.index,
    )


# ── Penalty (감점 요소) ────────────────────────────────────────────────────
def calc_penalty(df: pd.DataFrame, liquidity: pd.Series,
                 vai_spike: pd.Series) -> pd.Series:
    """
    윗꼬리 / 장중밀림 / 단발거래량 / 저유동성 → 차감(0.5~1.0).
    분석 §2.4 페널티 사상 계승.
    """
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()

    upper_wick = (h - np.maximum(o, c)) / rng          # 윗꼬리 비중
    pos_in_range = (c - l) / rng                        # 종가의 캔들 내 위치

    pen = pd.Series(0.0, index=df.index)
    pen += np.where(upper_wick > 0.5, 0.5, 0.0)         # 긴 윗꼬리
    pen += np.where(pos_in_range < 0.33, 0.5, 0.0)      # 장중밀림(종가 하단)
    pen += np.where(vai_spike, 1.0, 0.0)                # 단발 거래량 펌핑
    pen += np.where(liquidity < 0.7, 0.5, 0.0)          # 저유동성
    return pen.fillna(0.0)


# ── 통합 ───────────────────────────────────────────────────────────────────
def calculate_indicators(df: pd.DataFrame, market: Market = Market.US) -> pd.DataFrame:
    """
    표준 OHLCV(+Turnover) → 전 지표 부착.
    ★ market 인자를 끝까지 전달(통화버그 수정의 본질).
    """
    df = df.copy()
    c = df["Close"]

    df["ema12"] = _ema(c, 12)
    df["ema26"] = _ema(c, 26)
    df["ema50"] = _ema(c, 50)
    df["ema200"] = _ema(c, 200)
    df["rsi"] = _rsi(c)
    df["atr"] = _atr(df)
    df["adx"] = _adx(df)

    # 거래대금 보강(없으면 생성)
    if "Turnover" not in df.columns:
        df["Turnover"] = df["Close"] * df["Volume"]
    # 유동성 baseline: 5일 평균 거래대금 (분석: tv_ma5)
    tv_ma5 = df["Turnover"].rolling(5).mean().bfill()
    df["liquidity_score"] = calc_liquidity_score(tv_ma5, market)

    vai = calc_vai(df["Volume"])
    df = pd.concat([df, vai], axis=1)

    # 상대거래량(RVOL, 일봉) — shift(1) baseline
    df["rvol"] = (df["Volume"] /
                  df["Volume"].shift(1).rolling(20).mean()).fillna(1.0)

    df["penalty"] = calc_penalty(df, df["liquidity_score"], df["vai_spike"])
    df["market"] = market.value
    return df
