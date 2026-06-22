"""
전략 점수화 (US_KR_)CLAUD strategies.py 설계 계승).

Effective_Score = Technical_Score × Liquidity_Score − Penalty
  Technical_Score = Money_Score + Price_Score
  ★ Money_Score == 0 이면 Price_Score 최대 2.0 Cap
    ("거래량 없이 기술지표만으로 고득점" 구조적 차단 — 분석 §2.2)

Money 전략 3 (자금/거래량)  + Price 전략 7 (가격/구조).
가중치는 분석 문서 명시값(2.0/1.5/1.0) — 백테스트 전 가정치.

매도(Exit) 신호 5종 포함.

원본 소스가 부재하여 문서 스펙에 충실하게 재구현했다. 각 전략의 판정식은
주석으로 명시하며, 백테스트로 재검증함을 전제로 한다.
"""

from __future__ import annotations

import pandas as pd

from stock_auto.config.settings import (
    WEIGHTS, VAI_BREAKOUT, VAI_SUSTAIN,
    BUY_EFFECTIVE_MIN, BUY_EFFECTIVE_ALT, BUY_MONEY_MIN_ALT,
)


# ── Money 전략 (3) — 자금이 실제로 밀어주는가 ──────────────────────────────
def _money_signals(df: pd.DataFrame) -> pd.DataFrame:
    last = df.iloc[-1]
    sig = {}
    # M1(강): 돌파 거래량 + 지속성 + 상승 마감
    sig["M1_breakout_vol"] = (
        last["vai"] >= VAI_BREAKOUT and bool(last["vai_sustain"])
        and last["Close"] > last["Open"]
    )
    # M2(중): 점진적 거래량 증가(누적 매집) — 5일 거래대금 증가 추세
    tv5 = df["Turnover"].rolling(5).mean()
    sig["M2_accumulation"] = (
        len(tv5) > 6 and tv5.iloc[-1] > tv5.iloc[-6]
        and last["vai"] >= VAI_SUSTAIN
    )
    # M3(약): 유동성 양호 + 거래량 평균 이상
    sig["M3_liquidity_ok"] = (
        last["liquidity_score"] >= 0.7 and last["rvol"] >= 1.0
    )
    return pd.Series(sig)


def _money_score(sig: pd.Series) -> float:
    score = 0.0
    if sig["M1_breakout_vol"]:
        score += WEIGHTS["money_strong"]
    if sig["M2_accumulation"]:
        score += WEIGHTS["money_mid"]
    if sig["M3_liquidity_ok"]:
        score += WEIGHTS["money_weak"]
    return score


# ── Price 전략 (7) — 가격 구조가 유리한가 ──────────────────────────────────
def _price_signals(df: pd.DataFrame) -> pd.Series:
    last, prev = df.iloc[-1], df.iloc[-2]
    sig = {}
    # P1(강): EMA 정배열 (12>26>50)
    sig["P1_ma_align"] = last["ema12"] > last["ema26"] > last["ema50"]
    # P2(강): 박스 상단 돌파 (전고 20일 돌파)
    hh20 = df["High"].shift(1).rolling(20).max().iloc[-1]
    sig["P2_breakout"] = last["Close"] > hh20
    # P3(중): 골든크로스 직후 (ema12가 ema26 상향 돌파)
    sig["P3_golden"] = prev["ema12"] <= prev["ema26"] and last["ema12"] > last["ema26"]
    # P4(중): 눌림목 회복 (ema26 지지 후 반등)
    sig["P4_pullback"] = (
        last["Close"] > last["ema26"]
        and df["Low"].iloc[-3:].min() <= df["ema26"].iloc[-3:].min() * 1.01
    )
    # P5(중): RSI 회복 (과매도 탈출 30→상향)
    sig["P5_rsi_recover"] = prev["rsi"] < 35 and last["rsi"] > 40
    # P6(약): 스퀴즈 이탈 (EMA 수렴 후 확장) — 코인봇 convergence 사상 차용
    gap = (df["ema12"] - df["ema26"]).abs() / df["Close"]
    sig["P6_squeeze_release"] = (
        gap.iloc[-2] < gap.shift(1).rolling(20).mean().iloc[-2]
        and gap.iloc[-1] > gap.iloc[-2]
    )
    # P7(약): 추세 강도 (ADX≥25)
    sig["P7_adx_strong"] = last["adx"] >= 25
    return pd.Series(sig)


def _price_score(sig: pd.Series) -> float:
    score = 0.0
    for k in ("P1_ma_align", "P2_breakout"):
        if sig[k]:
            score += WEIGHTS["price_strong"]
    for k in ("P3_golden", "P4_pullback", "P5_rsi_recover"):
        if sig[k]:
            score += WEIGHTS["price_mid"]
    for k in ("P6_squeeze_release", "P7_adx_strong"):
        if sig[k]:
            score += WEIGHTS["price_weak"]
    return score


# ── Effective Score ────────────────────────────────────────────────────────
def score_symbol(df: pd.DataFrame) -> dict:
    """
    단일 종목(지표 부착된 df) → 점수 dict.
    df는 calculate_indicators()를 거친 상태여야 한다.
    """
    if len(df) < 60:
        raise ValueError("최소 60봉 필요 (지표 안정화)")

    last = df.iloc[-1]
    msig = _money_signals(df)
    psig = _price_signals(df)
    money = _money_score(msig)
    price = _price_score(psig)

    # ★ Money Cap: 자금 신호 없으면 가격점수 최대 2.0
    if money == 0.0:
        price = min(price, 2.0)

    technical = money + price
    liquidity = float(last["liquidity_score"])
    penalty = float(last["penalty"])
    effective = technical * liquidity - penalty

    return {
        "money_score": money,
        "price_score": price,
        "technical_score": technical,
        "liquidity_score": liquidity,
        "penalty": penalty,
        "effective_score": effective,
        "close": float(last["Close"]),
        "atr": float(last["atr"]),
        "rsi": float(last["rsi"]),
        "adx": float(last["adx"]),
        "vai": float(last["vai"]),
        "signals": {**msig.to_dict(), **psig.to_dict()},
    }


def is_buy(result: dict, sector_status: int | None = None,
           index_regime_ok: bool = True) -> bool:
    """
    매수 판정 (분석 문서 임계 + §3.2 섹터 반영 + §3.7 지수 게이트).
    - Effective ≥ 4.0  또는  (Effective ≥ 2.5 & Money ≥ 1.5)
    - 섹터 상태 0(하락위험)이면 차단 (분석 §3.2)
    - 지수 매크로 레짐 불가면 차단 (분석 §3.7 AND 게이트)
    """
    if not index_regime_ok:
        return False
    if sector_status is not None and sector_status <= 0:
        return False
    eff, money = result["effective_score"], result["money_score"]
    return (eff >= BUY_EFFECTIVE_MIN) or (
        eff >= BUY_EFFECTIVE_ALT and money >= BUY_MONEY_MIN_ALT
    )


# ── 매도(Exit) 신호 5종 ────────────────────────────────────────────────────
def sell_signals(df: pd.DataFrame) -> dict:
    """보유 종목 청산 신호 (분석 §2.6 매도 5종)."""
    last, prev = df.iloc[-1], df.iloc[-2]
    out = {}
    # S1: 데드크로스 (ema12 < ema26 하향 전환)
    out["S1_deadcross"] = prev["ema12"] >= prev["ema26"] and last["ema12"] < last["ema26"]
    # S2: 종가가 ema26 이탈
    out["S2_break_ema26"] = last["Close"] < last["ema26"]
    # S3: RSI 과열 후 꺾임 (70 위에서 하락)
    out["S3_rsi_rollover"] = prev["rsi"] > 70 and last["rsi"] < prev["rsi"]
    # S4: 분배성 거래량 (대량 거래 + 음봉)
    out["S4_distribution"] = last["vai"] >= VAI_BREAKOUT and last["Close"] < last["Open"]
    # S5: 추세 소멸 (ADX 하락 + 정배열 붕괴)
    out["S5_trend_fade"] = last["adx"] < prev["adx"] and not (
        last["ema12"] > last["ema26"])
    out["any"] = any(v for k, v in out.items())
    return out
