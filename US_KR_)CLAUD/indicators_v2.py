"""
indicators_v2.py — 보조지표 계산 모듈 (v2.1 - 거래량 품질 지표 강화)
=====================================================================
[v2.1 변경사항]
- VAI (Volume Acceleration Index) 추가 — Data Leakage 방지 (shift 적용)
- Liquidity Score 추가 — 연속함수로 Cliff Effect 완화
- Upper Wick Penalty 지표 추가
- Trading Value (거래대금) 계산 추가
"""

import pandas as pd
import numpy as np


# ============================================================
# 1. 전처리 (Pre-processing)
# ============================================================

def check_and_fix_columns(df):
    """컬럼명 표준화 (대소문자 호환성)"""
    df = df.copy()
    col_map = {c.lower(): c for c in df.columns}
    required = {
        'close': 'Close', 'open': 'Open',
        'high': 'High', 'low': 'Low', 'volume': 'Volume'
    }
    rename_dict = {}
    for req_lower, req_std in required.items():
        if req_lower in col_map:
            rename_dict[col_map[req_lower]] = req_std
    df = df.rename(columns=rename_dict)
    missing = [c for c in required.values() if c not in df.columns]
    if missing:
        return None
    return df


def prepare_datetime_index(df):
    """날짜 인덱스 처리"""
    if isinstance(df.index, pd.DatetimeIndex):
        df.index.name = 'Date'
        return df.sort_index()

    date_candidates = ['Date', 'date', 'DATE', '일자', 'time', 'Time']
    for col in date_candidates:
        if col in df.columns:
            try:
                df[col] = pd.to_datetime(df[col])
                df = df.set_index(col)
                df.index.name = 'Date'
                return df.sort_index()
            except Exception:
                continue

    try:
        first_col = df.columns[0]
        temp_date = pd.to_datetime(df[first_col], errors='coerce')
        if temp_date.notna().sum() > len(df) * 0.8:
            df[first_col] = temp_date
            df = df.set_index(first_col)
            df.index.name = 'Date'
            return df.sort_index()
    except Exception:
        pass

    if df.index.dtype == 'object':
        try:
            temp_index = pd.to_datetime(df.index, errors='coerce')
            if temp_index.notna().sum() > len(df) * 0.8:
                df.index = temp_index
                df.index.name = 'Date'
                return df.sort_index()
        except Exception:
            pass

    return None


# ============================================================
# 2. 핵심 지표 계산 (Core Indicators)
# ============================================================

def calculate_indicators(df):
    """
    보조지표 계산
    
    [v2.1 추가 지표]
    - VAI (Volume Acceleration Index) — 2단계 거래량 가속도
    - Liquidity_Score — 연속함수 기반 거래대금 점수
    - Upper_Wick_Ratio — 윗꼬리 비율 (매도 압력)
    - Trading_Value — 거래대금 (Close × Volume)
    """
    # [1] 전처리
    df = prepare_datetime_index(df)
    if df is None:
        return None
    df = check_and_fix_columns(df)
    if df is None or len(df) < 60:
        return None

    for col in ['Close', 'Open', 'High', 'Low', 'Volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    C, H, L, V, O = df['Close'], df['High'], df['Low'], df['Volume'], df['Open']

    # ── 이동평균 ──
    df['MA5'] = C.rolling(5).mean()
    df['MA10'] = C.rolling(10).mean()
    df['MA20'] = C.rolling(20).mean()
    df['MA60'] = C.rolling(60).mean()
    df['MA120'] = C.rolling(120).mean()
    df['EMA12'] = C.ewm(span=12, adjust=False).mean()
    df['EMA20'] = C.ewm(span=20, adjust=False).mean()
    df['EMA26'] = C.ewm(span=26, adjust=False).mean()
    df['EMA60'] = C.ewm(span=60, adjust=False).mean()

    # 기존 호환 별칭
    df['close_20_ema'] = df['EMA20']
    df['close_60_ema'] = df['EMA60']

    # ── MACD ──
    df['macd_line'] = df['EMA12'] - df['EMA26']
    df['macd_signal'] = df['macd_line'].ewm(span=9, adjust=False).mean()
    df['macdh'] = df['macd_line'] - df['macd_signal']

    # ── RSI (Wilder) ──
    delta = C.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    df['rsi_14'] = 100 - (100 / (1 + rs))

    # ── Stochastic ──
    low14 = L.rolling(14).min()
    high14 = H.rolling(14).max()
    df['stoch_k'] = 100 * (C - low14) / (high14 - low14 + 1e-9)
    df['stoch_d'] = df['stoch_k'].rolling(3).mean()

    # ── Williams %R ──
    df['williams_r'] = -100 * (high14 - C) / (high14 - low14 + 1e-9)

    # ── Bollinger Bands ──
    sma20 = C.rolling(20).mean()
    std20 = C.rolling(20).std()
    df['boll'] = sma20
    df['boll_ub'] = sma20 + std20 * 2
    df['boll_lb'] = sma20 - std20 * 2
    df['bb_width'] = (df['boll_ub'] - df['boll_lb']) / (df['boll'] + 1e-9)
    df['bb_pctb'] = (C - df['boll_lb']) / (df['boll_ub'] - df['boll_lb'] + 1e-9)

    # ── Keltner Channel ──
    tr = _calc_true_range(df)
    kc_atr = tr.ewm(span=20, adjust=False).mean()
    df['kc_upper'] = df['EMA20'] + kc_atr * 1.5
    df['kc_lower'] = df['EMA20'] - kc_atr * 1.5
    df['squeeze_on'] = (df['boll_lb'] > df['kc_lower']) & (df['boll_ub'] < df['kc_upper'])

    # ── ROC ──
    df['roc_12'] = C.pct_change(periods=12) * 100

    # ── ADX, +DI, -DI ──
    up = H - H.shift(1)
    down = L.shift(1) - L
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr_s = tr.ewm(alpha=1/14, adjust=False).mean()
    p_dm_s = pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean()
    m_dm_s = pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False).mean()
    df['pdi'] = 100 * (p_dm_s / (tr_s + 1e-9))
    df['mdi'] = 100 * (m_dm_s / (tr_s + 1e-9))
    dx = 100 * abs(df['pdi'] - df['mdi']) / (df['pdi'] + df['mdi'] + 1e-9)
    df['adx'] = dx.ewm(alpha=1/14, adjust=False).mean()

    # ── ATR ──
    df['atr'] = tr.ewm(alpha=1/14, adjust=False).mean()

    # ── Ichimoku Cloud ──
    tenkan = (H.rolling(9).max() + L.rolling(9).min()) / 2
    kijun = (H.rolling(26).max() + L.rolling(26).min()) / 2
    df['ichimoku_tenkan'] = tenkan
    df['ichimoku_kijun'] = kijun
    df['ichimoku_senkou_a'] = ((tenkan + kijun) / 2).shift(26)
    df['ichimoku_senkou_b'] = ((H.rolling(52).max() + L.rolling(52).min()) / 2).shift(26)

    # ═══════════════════════════════════════════
    # ★ 거래량 품질 지표 (v2.1 핵심 추가)
    # ═══════════════════════════════════════════

    # ── 기본 거래량 ──
    df['vol_ma20'] = V.rolling(20).mean()
    df['rvol'] = V / (df['vol_ma20'] + 1e-9)

    # ── 거래대금 (Trading Value) ──
    df['trading_value'] = C * V

    # ── VAI (Volume Acceleration Index) ── ★핵심
    # Gemini 피드백 반영: shift(1)로 Data Leakage 방지
    # 1단: 오늘 vs 어제까지 3일 평균
    vol_ma3_prev = V.shift(1).rolling(3).mean()  # 어제까지의 3일 평균
    df['vai_stage1'] = V / (vol_ma3_prev + 1e-9)

    # 2단: 어제까지 3일 평균 vs 어제까지 20일 평균
    vol_ma20_prev = V.shift(1).rolling(20).mean()  # 어제까지의 20일 평균
    df['vai_stage2'] = vol_ma3_prev / (vol_ma20_prev + 1e-9)

    # VAI 종합: 2단 모두 상승 = 자금 유입이 '쌓이는' 상태
    df['vai_sustained'] = (df['vai_stage1'] > 1.5) & (df['vai_stage2'] > 1.2)
    # 단발성: 1단만 높고 2단 낮음
    df['vai_spike_only'] = (df['vai_stage1'] > 2.0) & (df['vai_stage2'] < 1.1)

    # ── Liquidity Score (연속함수) ── ★핵심
    # GPT 피드백의 계단식 + Gemini 피드백의 연속함수 결합
    # sigmoid 형태로 부드러운 전환
    # 기준: 미국장 USD 거래대금 (한국장은 별도 처리)
    tv_ma5 = df['trading_value'].rolling(5).mean()  # 5일 평균 거래대금
    df['liquidity_score'] = _calc_liquidity_score(tv_ma5)

    # ── OBV ──
    direction = np.where(C > C.shift(1), 1, np.where(C < C.shift(1), -1, 0))
    df['obv'] = (direction * V).cumsum()
    df['obv_ma20'] = df['obv'].rolling(20).mean()

    # ── MFI ──
    tp = (H + L + C) / 3
    rmf = tp * V
    tp_shift = tp.shift(1)
    pos_flow = np.where(tp > tp_shift, rmf, 0)
    neg_flow = np.where(tp < tp_shift, rmf, 0)
    pos_mf = pd.Series(pos_flow, index=df.index).rolling(14).sum()
    neg_mf = pd.Series(neg_flow, index=df.index).rolling(14).sum()
    df['mfi_14'] = 100 - (100 / (1 + pos_mf / (neg_mf + 1e-9)))

    # ═══════════════════════════════════════════
    # ★ Penalty 지표 (6점 정체 종목 제거용)
    # ═══════════════════════════════════════════

    # ── Upper Wick Ratio (윗꼬리 비율) ──
    body = abs(C - O)
    candle_range = H - L + 1e-9
    upper_wick = H - pd.concat([O, C], axis=1).max(axis=1)
    df['upper_wick_ratio'] = upper_wick / candle_range

    # ── High-to-Close Drop (고가 대비 종가 하락률) ──
    df['high_close_drop'] = (H - C) / (H + 1e-9) * 100

    # ── 수익률 ──
    df['pct_change_1d'] = C.pct_change(1)
    df['pct_change_5d'] = C.pct_change(5)
    df['pct_change_20d'] = C.pct_change(20)

    df = df.dropna(subset=['MA60', 'adx', 'rsi_14'])

    return df


def _calc_true_range(df):
    """True Range 계산"""
    tr1 = df['High'] - df['Low']
    tr2 = abs(df['High'] - df['Close'].shift(1))
    tr3 = abs(df['Low'] - df['Close'].shift(1))
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def _calc_liquidity_score(trading_value_series, market='US'):
    """
    Liquidity Score — 연속함수 (Cliff Effect 완화)
    
    sigmoid 변형: score = min_score + (max_score - min_score) * sigmoid((tv - center) / scale)
    
    [미국장 기준 — USD]
    거래대금 500만 이하  → ~0.4
    거래대금 3000만      → ~0.8
    거래대금 1억         → ~1.0
    거래대금 5억 이상    → ~1.3
    
    [한국장 기준 — KRW]  
    거래대금 30억 이하   → ~0.4
    거래대금 100억       → ~0.8
    거래대금 300억       → ~1.0
    거래대금 1000억 이상 → ~1.3
    """
    if market == 'KR':
        center = 15_000_000_000   # 150억원 중심
        scale = 8_000_000_000     # 80억원 스케일
    else:
        center = 50_000_000       # 5000만 USD 중심
        scale = 30_000_000        # 3000만 USD 스케일

    # sigmoid: 0~1 범위
    z = (trading_value_series - center) / (scale + 1e-9)
    sigmoid = 1 / (1 + np.exp(-z))

    # 0.4 ~ 1.3 범위로 스케일링
    score = 0.4 + 0.9 * sigmoid

    return score.clip(0.3, 1.4)  # 안전 범위 제한


# ============================================================
# 3. 캔들 패턴 분석
# ============================================================

def analyze_candlestick(df):
    """캔들 패턴 및 이평선 지지/저항 분석"""
    C, O, H, L = df['Close'], df['Open'], df['High'], df['Low']

    df['Body'] = abs(C - O)
    df['Upper_Shadow'] = H - pd.concat([O, C], axis=1).max(axis=1)
    df['Lower_Shadow'] = pd.concat([O, C], axis=1).min(axis=1) - L
    df['Avg_Body'] = df['Body'].rolling(20).mean()

    prev_c, prev_o = C.shift(1), O.shift(1)

    # 상승 장악형
    df['Candle_Engulfing'] = (
        (prev_c < prev_o) & (C > O) & (C > prev_o) & (O < prev_c)
    )
    # 망치형
    df['Candle_Hammer'] = (
        (df['Lower_Shadow'] > df['Body'] * 2) & (df['Upper_Shadow'] < df['Body'] * 0.5)
    )
    # Morning Star
    prev2_c, prev2_o = C.shift(2), O.shift(2)
    small_body = df['Body'].shift(1) < df['Avg_Body'].shift(1) * 0.5
    df['Candle_MorningStar'] = (
        (prev2_c < prev2_o) & small_body & (C > O) &
        (C > (prev2_o + prev2_c) / 2)
    )
    # 하락 장악형
    df['Candle_BearEngulfing'] = (
        (prev_c > prev_o) & (C < O) & (O > prev_c) & (C < prev_o)
    )

    # 20일선 지지
    ma20_slope = df['EMA20'].diff()
    df['MA20_Bounce'] = (
        (L <= df['EMA20']) & (C > df['EMA20']) & (ma20_slope > 0)
    )
    # 20일선 붕괴
    df['MA20_Break'] = (
        (C < df['EMA20']) & (prev_c > df['EMA20'].shift(1))
    )
    # 다이버전스
    local_high_price = H.rolling(20).max()
    local_high_rsi = df['rsi_14'].rolling(20).max()
    df['Bearish_Div'] = (
        (H >= local_high_price) & (df['rsi_14'] < local_high_rsi) & (df['rsi_14'] < 70)
    )
    local_low_price = L.rolling(20).min()
    local_low_rsi = df['rsi_14'].rolling(20).min()
    df['Bullish_Div'] = (
        (L <= local_low_price) & (df['rsi_14'] > local_low_rsi) & (df['rsi_14'] > 30)
    )

    return df


# ============================================================
# 4. 매물대 분석
# ============================================================

def analyze_volume_profile(df, period=120, bins=50):
    """매물대 분석"""
    if df is None or len(df) < 10:
        return None
    subset = df.iloc[-period:].copy()
    price_min, price_max = subset['Low'].min(), subset['High'].max()
    if price_min == price_max:
        return None

    intervals = np.linspace(price_min, price_max, bins + 1)
    subset['Price_Bin'] = pd.cut(subset['Close'], bins=intervals, labels=False, include_lowest=True)
    vol_profile = subset.groupby('Price_Bin')['Volume'].sum()

    current_price = df.iloc[-1]['Close']
    current_bin_idx = max(0, min(np.searchsorted(intervals, current_price) - 1, bins - 1))

    if vol_profile.empty:
        return None

    max_vol_bin = vol_profile.idxmax()
    poc_price = (intervals[int(max_vol_bin)] + intervals[int(max_vol_bin) + 1]) / 2

    avg_vol = vol_profile.mean()
    curr_density = vol_profile.get(current_bin_idx, 0) / (avg_vol + 1e-9)

    upper_bins = range(current_bin_idx + 1, min(current_bin_idx + 6, bins))
    upper_vol = vol_profile.loc[vol_profile.index.isin(upper_bins)].sum()
    lower_bins = range(max(0, current_bin_idx - 5), current_bin_idx)
    lower_vol = vol_profile.loc[vol_profile.index.isin(lower_bins)].sum()

    return {
        'POC_Price': round(poc_price, 2),
        'In_POC_Zone': max_vol_bin == current_bin_idx,
        'Vol_Density': round(curr_density, 3),
        'Resistance_Ratio': round(upper_vol / (lower_vol + 1e-9), 3),
        'Dist_to_POC': round((current_price - poc_price) / poc_price, 4),
    }


def check_immediate_hurdle(df, lookback=40):
    """시가 배팅용 초단기 매물대 분석"""
    if df is None or len(df) < lookback:
        return None
    subset = df.iloc[-lookback:].copy()
    cc = subset.iloc[-1]['Close']
    mask_res = (subset['Close'] > cc) & (subset['Close'] <= cc * 1.05)
    resistance_vol = subset.loc[mask_res, 'Volume'].sum()
    mask_sup = (subset['Close'] >= cc * 0.97) & (subset['Close'] <= cc)
    support_vol = subset.loc[mask_sup, 'Volume'].sum()
    avg_vol = subset['Volume'].mean() * lookback

    return {
        'Hurdle_Score': round(resistance_vol / (avg_vol + 1e-9), 4),
        'SR_Ratio': round(999.0 if resistance_vol == 0 else support_vol / resistance_vol, 3),
    }


# ============================================================
# 5. ATR 기반 동적 손절/타겟
# ============================================================

def calculate_dynamic_levels(df, regime=1):
    """Regime별 ATR 기반 손절/타겟"""
    if df is None or len(df) < 14:
        return None
    last = df.iloc[-1]
    atr, close = last['atr'], last['Close']

    params = {
        4: {'tp': 2.5, 'sl': 1.5}, 3: {'tp': 2.0, 'sl': 1.2},
        2: {'tp': 1.5, 'sl': 1.0}, 1: {'tp': 1.0, 'sl': 0.8},
        0: {'tp': 0.0, 'sl': 0.0},
    }.get(regime, {'tp': 1.5, 'sl': 1.0})

    target = round(close + atr * params['tp'], 2)
    stop = round(close - atr * params['sl'], 2)

    return {
        'target_price': target, 'stop_price': stop,
        'target_pct': round((target - close) / close * 100, 2),
        'stop_pct': round((close - stop) / close * 100, 2),
        'rr_ratio': round(params['tp'] / (params['sl'] + 1e-9), 2),
    }


# ============================================================
# 6. VWAP (분봉용)
# ============================================================

def calculate_vwap(df):
    cum_vol = df['Volume'].cumsum()
    cum_vol_price = (df['Volume'] * (df['High'] + df['Low'] + df['Close']) / 3).cumsum()
    df['VWAP'] = cum_vol_price / (cum_vol + 1e-9)
    return df
