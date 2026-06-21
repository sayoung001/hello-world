"""
strategies.py — 매매 전략 모듈 (v2.1 — 실효 점수 체계)
==========================================================
[v2.1 핵심 변경]
  기존: Composite_Score = 기술적 신호 개수 합산
  변경: Effective_Score = Technical × Liquidity × (1 - Penalty)

  "기술적 가능성"이 아니라 "자금이 실제로 밀어줄 확률"을 측정

[구조]
  1. Money-driven Signals (자금 신호) — 가중치 1.5배
  2. Price-driven Signals (기술 신호) — 기본 가중치
  3. Money 신호 없으면 Price 신호 캡(Cap) 적용
  4. Liquidity_Score 곱셈 (거래대금 반영)
  5. Penalty 차감 (윗꼬리, 단발성 거래량 등)
"""

import pandas as pd
import numpy as np
from indicators_v2 import analyze_candlestick


# ============================================================
# 개별 전략 — Money-Driven (자금이 실제로 움직인 증거)
# ============================================================

def strategy_vol_breakout(df):
    """
    [MONEY] 거래량 돌파 — RVOL>2 + 20일 고가 갱신
    ★ VAI 2단 지속 조건 추가로 단발성 펌핑 걸러냄
    """
    high_20 = df['High'].rolling(20).max().shift(1)
    sig = (
        (df['rvol'] > 2.0) &
        (df['Close'] > df['Open']) &
        (df['Close'] > high_20) &
        (df['adx'] > 20) &
        (~df['vai_spike_only'])  # ★ 단발성 펌핑 배제
    )
    df['Strat_VolBreakout'] = sig
    return df


def strategy_vol_pump_sustained(df):
    """
    [MONEY] 지속적 자금 유입 — VAI 2단 지속 + 양봉
    ★ 기존 Vol_Pump를 VAI로 대체한 개선 버전
    """
    sig = (
        (df['vai_sustained']) &        # 2단 지속 상승
        (df['Close'] > df['Open']) &   # 양봉
        (df['Close'] > df['Close'].shift(1)) &  # 전일 대비 상승
        (df['liquidity_score'] > 0.7)  # 최소 유동성
    )
    df['Strat_VolPumpSustained'] = sig
    return df


def strategy_money_flow_surge(df):
    """
    [MONEY] 자금 유입 급증 — MFI + OBV 동시 확인
    """
    obv_rising = df['obv'] > df['obv_ma20']
    mfi_strong = df['mfi_14'] > 60
    sig = (
        obv_rising & mfi_strong &
        (df['rvol'] > 1.5) &
        (df['Close'] > df['EMA20'])
    )
    df['Strat_MoneyFlowSurge'] = sig
    return df


# ============================================================
# 개별 전략 — Price-Driven (기술적 지표)
# ============================================================

def strategy_mean_reversion(df):
    """[PRICE] 평균 회귀 — RSI+BB 과매도 반등"""
    sig = (
        (df['rsi_14'] < 30) &
        (df['bb_pctb'] < 0.1) &
        (df['mfi_14'] < 35) &
        (df['Close'] > df['Open'])
    )
    df['Strat_MeanReversion'] = sig
    return df


def strategy_gap_momentum(df):
    """[PRICE] 갭 모멘텀 — 1.5%+ 갭업 후 지속"""
    prev_c = df['Close'].shift(1)
    gap_pct = (df['Open'] - prev_c) / (prev_c + 1e-9)
    sig = (
        (gap_pct > 0.015) &
        (df['Close'] >= df['Open']) &
        (df['rvol'] > 1.0)
    )
    df['Strat_GapMomentum'] = sig
    return df


def strategy_pullback_buy(df):
    """[PRICE] 눌림목 매수 — EMA20 터치 후 반등"""
    ema20_slope = df['EMA20'].diff()
    sig = (
        (df['EMA20'] > df['EMA60']) &
        (df['Low'] <= df['EMA20'] * 1.005) &
        (df['Close'] > df['EMA20']) &
        (ema20_slope > 0) &
        (df['rsi_14'] > 40) & (df['rsi_14'] < 60)
    )
    df['Strat_PullbackBuy'] = sig
    return df


def strategy_macd_reversal(df):
    """[PRICE] MACD 히스토그램 반전"""
    prev_macdh = df['macdh'].shift(1)
    sig = (
        (prev_macdh < 0) & (df['macdh'] > 0) &
        (df['macd_line'] > df['macd_signal']) &
        (df['Close'] > df['EMA60']) &
        (df['obv'] > df['obv_ma20'])
    )
    df['Strat_MACDReversal'] = sig
    return df


def strategy_squeeze_breakout(df):
    """[PRICE] Squeeze 돌파 — BB+KC 수렴 후 폭발"""
    if 'squeeze_on' not in df.columns:
        df['Strat_SqueezeBreakout'] = False
        return df
    squeeze_count = df['squeeze_on'].rolling(5).sum()
    prev_squeeze = squeeze_count.shift(1) >= 3
    sig = (
        prev_squeeze &
        (~df['squeeze_on']) &
        (df['Close'] > df['Open']) &
        (df['Close'] > df['boll_ub']) &
        (df['rvol'] > 1.5)
    )
    df['Strat_SqueezeBreakout'] = sig
    return df


def strategy_ichimoku_trend(df):
    """[PRICE] 일목균형표 추세"""
    if 'ichimoku_tenkan' not in df.columns:
        df['Strat_IchimokuTrend'] = False
        return df
    cloud_top = df[['ichimoku_senkou_a', 'ichimoku_senkou_b']].max(axis=1)
    sig = (
        (df['Close'] > df['ichimoku_tenkan']) &
        (df['ichimoku_tenkan'] > df['ichimoku_kijun']) &
        (df['Close'] > cloud_top)
    )
    df['Strat_IchimokuTrend'] = sig
    return df


def strategy_trend_continuation(df):
    """[PRICE] 완전 정배열 추세"""
    sig = (
        (df['MA5'] > df['MA10']) &
        (df['MA10'] > df['MA20']) &
        (df['MA20'] > df['MA60']) &
        (df['adx'] > 25) &
        (df['rsi_14'] > 50) & (df['rsi_14'] < 70) &
        (df['obv'] > df['obv_ma20'])
    )
    df['Strat_TrendContinuation'] = sig
    return df


# ============================================================
# 전략 분류 및 가중치 설정
# ============================================================

# Money-Driven: 자금이 실제로 움직인 증거 (가중치 1.5배)
MONEY_STRATEGIES = {
    'Strat_VolBreakout':       {'weight': 2.0,  'hold': '1~3일', 'style': '단타',   'tp_atr': 2.0, 'sl_atr': 1.0},
    'Strat_VolPumpSustained':  {'weight': 2.0,  'hold': '2~5일', 'style': '단타',   'tp_atr': 2.0, 'sl_atr': 1.0},
    'Strat_MoneyFlowSurge':    {'weight': 1.5,  'hold': '3~7일', 'style': '중단기', 'tp_atr': 2.0, 'sl_atr': 1.2},
}

# Price-Driven: 기술적 패턴
PRICE_STRATEGIES = {
    'Strat_MeanReversion':     {'weight': 1.5, 'hold': '1~3일',   'style': '단타',   'tp_atr': 1.5, 'sl_atr': 1.0},
    'Strat_GapMomentum':       {'weight': 1.0, 'hold': '당일~1일', 'style': '단타',   'tp_atr': 1.5, 'sl_atr': 0.8},
    'Strat_PullbackBuy':       {'weight': 1.5, 'hold': '3~7일',   'style': '중단기', 'tp_atr': 2.0, 'sl_atr': 1.2},
    'Strat_MACDReversal':      {'weight': 1.0, 'hold': '3~10일',  'style': '중단기', 'tp_atr': 2.0, 'sl_atr': 1.5},
    'Strat_SqueezeBreakout':   {'weight': 1.5, 'hold': '3~7일',   'style': '중단기', 'tp_atr': 2.5, 'sl_atr': 1.0},
    'Strat_IchimokuTrend':     {'weight': 1.0, 'hold': '10~20일', 'style': '스윙',   'tp_atr': 3.0, 'sl_atr': 1.5},
    'Strat_TrendContinuation': {'weight': 1.5, 'hold': '5~20일',  'style': '스윙',   'tp_atr': 3.0, 'sl_atr': 1.2},
}

ALL_STRATEGIES = {**MONEY_STRATEGIES, **PRICE_STRATEGIES}

# 기존 호환 전략
LEGACY_STRATEGIES = ['Trend_Up', 'Momentum', 'Oversold', 'Vol_Explode', 'Vol_Pump', 'Candle_Buy']


# ============================================================
# ★ 핵심: 실효 점수 계산 (Effective Score)
# ============================================================

def _calculate_penalty(row):
    """
    Penalty 계산 — "안 가는 6점"을 구조적으로 제거
    
    [페널티 조건]
    1. 윗꼬리 비율 > 0.6 → -0.5 (매도 압력 큼)
    2. 고가→종가 하락 > 1.5% → -0.5 (장중 밀림)
    3. 단발성 거래량 (VAI spike only) → -1.0 (가장 큰 페널티)
    4. Liquidity Score < 0.5 → -0.5 (유동성 부족)
    """
    penalty = 0.0

    if row.get('upper_wick_ratio', 0) > 0.6:
        penalty += 0.5

    if row.get('high_close_drop', 0) > 1.5:
        penalty += 0.5

    if row.get('vai_spike_only', False):
        penalty += 1.0

    if row.get('liquidity_score', 1.0) < 0.5:
        penalty += 0.5

    return min(penalty, 3.0)  # 최대 3점 차감


def apply_all_strategies(df, regime=None):
    """
    모든 전략을 적용하고 실효 점수(Effective Score) 계산
    
    [점수 구조]
    ┌─────────────────────────────────────────────────────┐
    │ Technical_Score = Money_Score + Price_Score          │
    │   (단, Money_Score == 0이면 Price_Score 최대 2.0)    │
    │                                                     │
    │ Effective_Score = Technical_Score                    │
    │                 × Liquidity_Score                    │
    │                 - Penalty                            │
    └─────────────────────────────────────────────────────┘
    """
    if df is None or len(df) == 0:
        return None

    # ── 캔들 분석 ──
    df = analyze_candlestick(df)

    # ── 기존 전략 (호환 유지) ──
    df['Trend_Up'] = (
        (df['EMA20'] > df['EMA60']) & (df['adx'] > 20) & (df['obv'] > df['obv_ma20'])
    )
    df['Momentum'] = (df['macdh'] > df['macdh'].shift(1)) & (df['roc_12'] > 0)
    df['Oversold'] = (df['rsi_14'] < 30) & (df['mfi_14'] < 30)
    df['Vol_Explode'] = (df['bb_width'] > df['bb_width'].rolling(20).mean() * 1.5) & (df['Close'] > df['boll_ub'])
    df['Vol_Pump'] = (df['rvol'] > 2.0) & (df['Close'] > df['Open']) & (df['Close'] > df['Close'].shift(1))
    df['Candle_Buy'] = (
        (df['Close'] > df['EMA60']) &
        (df['Candle_Engulfing'] | df['Candle_Hammer'] | df['MA20_Bounce'])
    )
    df['Signal_Count'] = df[LEGACY_STRATEGIES].sum(axis=1).astype(int)

    # ── 신규 전략 적용 ──
    # Money-driven
    df = strategy_vol_breakout(df)
    df = strategy_vol_pump_sustained(df)
    df = strategy_money_flow_surge(df)
    # Price-driven
    df = strategy_mean_reversion(df)
    df = strategy_gap_momentum(df)
    df = strategy_pullback_buy(df)
    df = strategy_macd_reversal(df)
    df = strategy_squeeze_breakout(df)
    df = strategy_ichimoku_trend(df)
    df = strategy_trend_continuation(df)

    # ═══════════════════════════════════════════
    # ★ Money Score (자금 신호 — 가중치 높음)
    # ═══════════════════════════════════════════
    money_score = pd.Series(0.0, index=df.index)
    for name, cfg in MONEY_STRATEGIES.items():
        if name in df.columns:
            money_score += df[name].astype(float) * cfg['weight']
    df['Money_Score'] = money_score

    # ═══════════════════════════════════════════
    # ★ Price Score (기술 신호)
    # ═══════════════════════════════════════════
    price_score = pd.Series(0.0, index=df.index)
    for name, cfg in PRICE_STRATEGIES.items():
        if name in df.columns:
            price_score += df[name].astype(float) * cfg['weight']
    df['Price_Score'] = price_score

    # ═══════════════════════════════════════════
    # ★ Money Cap: 자금 신호 없으면 기술 신호 최대 2.0
    # ═══════════════════════════════════════════
    capped_price = price_score.copy()
    no_money = money_score < 0.5  # Money 신호 사실상 없음
    capped_price[no_money] = capped_price[no_money].clip(upper=2.0)

    # Technical Score = Money + Capped Price
    technical_score = money_score + capped_price
    df['Technical_Score'] = technical_score.round(2)

    # ═══════════════════════════════════════════
    # ★ Effective Score = Technical × Liquidity - Penalty
    # ═══════════════════════════════════════════
    liquidity = df['liquidity_score'].fillna(0.5)

    # Penalty 벡터화 계산
    penalty = pd.Series(0.0, index=df.index)
    penalty += (df['upper_wick_ratio'] > 0.6).astype(float) * 0.5
    penalty += (df['high_close_drop'] > 1.5).astype(float) * 0.5
    penalty += df['vai_spike_only'].astype(float) * 1.0
    penalty += (liquidity < 0.5).astype(float) * 0.5
    penalty = penalty.clip(upper=3.0)
    df['Penalty'] = penalty

    df['Effective_Score'] = (technical_score * liquidity - penalty).round(2)

    # ── 기존 호환 Composite_Score (Effective_Score로 대체) ──
    df['Composite_Score'] = df['Effective_Score']

    # ── 경고 수 ──
    df['Warning_Count'] = (
        df['MA20_Break'].astype(int) +
        df['Bearish_Div'].astype(int) +
        df.get('Candle_BearEngulfing', pd.Series(False, index=df.index)).astype(int)
    )

    # ═══════════════════════════════════════════
    # ★ 최종 매수 판단 (실효 점수 기반)
    # ═══════════════════════════════════════════
    regime_ok = True if regime is None else regime >= 1

    if regime_ok:
        df['Final_Buy'] = (
            (df['Effective_Score'] >= 4.0) |          # 실효 점수 4점 이상
            (
                (df['Effective_Score'] >= 2.5) &
                (df['Money_Score'] >= 1.5) &           # 자금 신호가 확인된 경우
                (df['liquidity_score'] >= 0.7)
            )
        ) & (df['Warning_Count'] < 2) & (df['Penalty'] < 2.0)
    else:
        df['Final_Buy'] = False

    df['Buy_Signal'] = df['Final_Buy']

    return df


# ============================================================
# 전략 정보 추출 헬퍼
# ============================================================

def get_active_strategies(row):
    """마지막 행에서 활성화된 전략 이름+스타일 반환"""
    active = []
    for name, cfg in ALL_STRATEGIES.items():
        if row.get(name, False):
            active.append({
                'name': name.replace('Strat_', ''),
                'style': cfg['style'],
                'hold': cfg['hold'],
                'tp_atr': cfg['tp_atr'],
                'sl_atr': cfg['sl_atr'],
                'is_money': name in MONEY_STRATEGIES,
            })
    return active


def calculate_strategy_levels(row, active_strategies):
    """활성 전략들의 ATR 기반 TP/SL 계산"""
    if not active_strategies or 'atr' not in row.index:
        return {'target': None, 'stop': None, 'rr_ratio': None}

    atr, close = row['atr'], row['Close']
    targets, stops = [], []
    for s in active_strategies:
        targets.append(close + atr * s['tp_atr'])
        stops.append(close - atr * s['sl_atr'])

    target = min(targets)  # 가장 보수적 타겟
    stop = max(stops)      # 가장 타이트한 손절
    risk = close - stop
    reward = target - close

    return {
        'target': round(target, 2), 'stop': round(stop, 2),
        'target_pct': round((target - close) / close * 100, 2),
        'stop_pct': round((close - stop) / close * 100, 2),
        'rr_ratio': round(reward / (risk + 1e-9), 2),
    }


# ============================================================
# 매도 전략 (Exit Signals)
# ============================================================

def check_exit_signals(df):
    """매도 신호 확인"""
    C = df['Close']
    df['Exit_RSI_OB'] = df['rsi_14'] > 80
    df['Exit_MA20_Break'] = df['MA20_Break']
    df['Exit_MACD_Dead'] = (
        (df['macd_line'] < df['macd_signal']) &
        (df['macd_line'].shift(1) > df['macd_signal'].shift(1))
    )
    df['Exit_BearEngulfing'] = df.get('Candle_BearEngulfing', pd.Series(False, index=df.index))
    df['Exit_Distribution'] = (
        (C < C.shift(1)) &
        (df['Volume'] > df['vol_ma20']) &
        (df['obv'] < df['obv_ma20'])
    )
    exit_cols = ['Exit_RSI_OB', 'Exit_MA20_Break', 'Exit_MACD_Dead',
                 'Exit_BearEngulfing', 'Exit_Distribution']
    df['Exit_Score'] = df[exit_cols].sum(axis=1).astype(int)
    df['Exit_Signal'] = df['Exit_Score'] >= 2
    return df
