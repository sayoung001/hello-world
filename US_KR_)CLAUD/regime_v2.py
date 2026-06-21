"""
regime_v2.py — 시장 국면(Regime) 분석 모듈 (개선판)
====================================================
[변경 이력]
- 기존 3단계(0/1/2) → 5단계(0~4)로 세분화
- ADX, 거래량, 변동성까지 종합 판단
- Regime 전환 감지 (Transition Detection) 추가
"""

import pandas as pd
import numpy as np


def add_market_regime(df):
    """
    시장 국면(Regime) 분류 — 5단계

    [Regime 정의]
    ┌───┬───────────┬──────────────────────────────────┐
    │ 4 │ 강한 상승  │ 정배열 + ADX>25 + 거래량 증가      │
    │ 3 │ 상승 초기  │ 정배열 (Close > MA20 > MA60)      │
    │ 2 │ 보합/횡보  │ Close ~ MA20/MA60 혼재            │
    │ 1 │ 하락 초기  │ 역배열 시작 (Close < MA20)         │
    │ 0 │ 강한 하락  │ 역배열 + ADX>25 + 거래량 증가      │
    └───┴───────────┴──────────────────────────────────┘
    """
    if df is None or len(df) < 60:
        return df

    C = df['Close']

    if 'MA20' not in df.columns:
        df['MA20'] = C.rolling(20).mean()
    if 'MA60' not in df.columns:
        df['MA60'] = C.rolling(60).mean()

    ma20, ma60 = df['MA20'], df['MA60']

    has_adx = 'adx' in df.columns
    has_rvol = 'rvol' in df.columns

    bullish_align = (C > ma20) & (ma20 > ma60)
    bearish_align = (C < ma20) & (ma20 < ma60)
    above_60 = C > ma60

    if has_adx and has_rvol:
        strong_trend = df['adx'] > 25
        vol_expanding = df['rvol'] > 1.2

        conditions = [
            bullish_align & strong_trend & vol_expanding,
            bullish_align,
            above_60,
            ~above_60 & ~bearish_align,
        ]
        choices = [4, 3, 2, 1]
    elif has_adx:
        strong_trend = df['adx'] > 25
        conditions = [
            bullish_align & strong_trend,
            bullish_align,
            above_60,
            ~above_60 & ~bearish_align,
        ]
        choices = [4, 3, 2, 1]
    else:
        conditions = [
            bullish_align,
            above_60,
        ]
        choices = [2, 1]

    df['Regime'] = np.select(conditions, choices, default=0)

    return df


def add_regime_simple(df):
    """기존 호환용 3단계 Regime (0/1/2)"""
    if df is None or len(df) < 60:
        return df

    if 'MA20' not in df.columns:
        df['MA20'] = df['Close'].rolling(20).mean()
    if 'MA60' not in df.columns:
        df['MA60'] = df['Close'].rolling(60).mean()

    conditions = [
        (df['Close'] > df['MA20']) & (df['MA20'] > df['MA60']),
        (df['Close'] > df['MA60']),
    ]
    df['Regime'] = np.select(conditions, [2, 1], default=0)
    return df


def detect_regime_transition(df, lookback=5):
    """
    Regime 전환 감지
    
    Returns: dict
        - 'current': 현재 Regime
        - 'prev': lookback일 전 Regime
        - 'transition': 'improving' / 'deteriorating' / 'stable'
        - 'days_in_regime': 현재 Regime 연속 일수
    """
    if 'Regime' not in df.columns or len(df) < lookback + 1:
        return {'current': -1, 'prev': -1, 'transition': 'unknown', 'days_in_regime': 0}

    current = int(df['Regime'].iloc[-1])
    prev = int(df['Regime'].iloc[-lookback])

    regimes = df['Regime'].values
    days = 1
    for i in range(len(regimes) - 2, -1, -1):
        if regimes[i] == current:
            days += 1
        else:
            break

    if current > prev:
        transition = 'improving'
    elif current < prev:
        transition = 'deteriorating'
    else:
        transition = 'stable'

    return {
        'current': current,
        'prev': prev,
        'transition': transition,
        'days_in_regime': days,
    }


# ============================================================
# Regime → 트레이딩 파라미터 매핑
# ============================================================

REGIME_TRADING_PARAMS = {
    4: {'size': 1.0, 'hold': 10, 'tp': 3.0, 'sl': 1.5, 'label': '강세(공격)'},
    3: {'size': 0.8, 'hold': 7,  'tp': 2.5, 'sl': 1.2, 'label': '상승초기'},
    2: {'size': 0.5, 'hold': 5,  'tp': 2.0, 'sl': 1.0, 'label': '보합'},
    1: {'size': 0.3, 'hold': 3,  'tp': 1.5, 'sl': 0.8, 'label': '하락초기'},
    0: {'size': 0.0, 'hold': 0,  'tp': 0.0, 'sl': 0.0, 'label': '약세(관망)'},
}


def get_trading_params(regime):
    """Regime에 따른 트레이딩 파라미터 반환"""
    return REGIME_TRADING_PARAMS.get(regime, REGIME_TRADING_PARAMS[2])
