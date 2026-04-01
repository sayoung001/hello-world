"""
btc_structure.py — Agent 1: BTC 구조 분석가
=============================================
BTC 가격 구조, 핵심 레벨, 청산 클러스터 분석.
분석 유형: Factual (사실 기반)

데이터 소스:
- Binance API (가격, 거래량, 멀티타임프레임)
- CoinGlass (청산 클러스터, OI, 펀딩레이트, LS비율, Taker, 공포탐욕)
- btc_filter.py (기존 BTC 7단계 상태 분석 재활용)
"""

from __future__ import annotations
import os
import pandas as pd
import numpy as np
from typing import Any

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage, RiskLevel


class BTCStructureAgent(AgentBase):
    """
    BTC 구조 분석가

    책임:
    - 주요 지지/저항 레벨 식별 (Volume Profile 근사 + 청산맵)
    - 청산 클러스터 위치 파악 (20x 레버리지 감안)
    - 돌파 확률 추정
    - OI/펀딩레이트/Taker/LS비율 기반 시장 과열 판단
    - BTCFilter 7단계 상태 통합
    """

    def __init__(self, cg_client=None, exchange=None, btc_filter=None):
        super().__init__(
            agent_id="agent_1_btc",
            agent_name="BTC 구조 분석가",
            role_description="BTC 가격 구조와 핵심 레벨을 분석하는 Factual 에이전트"
        )
        self._analysis_type = "factual"
        self.cg = cg_client
        if exchange is None:
            import ccxt
            exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        self.exchange = exchange
        self.btc_filter = btc_filter

    def _get_system_prompt(self) -> str:
        return """당신은 BTC 가격 구조 전문 분석가입니다.
20배 레버리지 선물 트레이딩 환경에서 BTC의 핵심 가격 레벨을 분석합니다.

분석 원칙:
- 사실적(Factual) 데이터에 기반한 객관적 분석
- 청산 클러스터 위치는 20x 레버리지 기준으로 평가 (가격 자석 효과)
- 돌파 확률은 보수적으로 추정 (생존 우선)
- 청산 중력(Gravity) 방향이 현재 포지션과 반대면 위험 신호

반드시 JSON 형식으로 출력하세요:
```json
{
  "btc_price": 현재가,
  "key_resistance": [저항 레벨들],
  "key_support": [지지 레벨들],
  "volume_profile_poc": 거래량 집중 가격대(POC),
  "breakout_probability": 상방돌파확률(0~1),
  "trend": "bullish|neutral|bearish",
  "trend_strength": "strong|moderate|weak",
  "oi_signal": "OI 분석 요약",
  "funding_signal": "펀딩레이트 분석 요약",
  "liquidation_gravity": "UP|DOWN|NEUTRAL",
  "liquidation_risk": "청산 클러스터 관련 위험 요약",
  "risk_level": "SAFE|CAUTION|DANGER",
  "confidence": 0.0~1.0,
  "reasoning": "종합 분석 근거"
}
```"""

    def collect_data(self) -> dict:
        """BTC 관련 데이터 수집 (API 직접 호출)"""
        data = {}

        # 1. Binance OHLCV (15분봉, 1시간봉, 4시간봉)
        for tf in ["15m", "1h", "4h"]:
            try:
                ohlcv = self.exchange.fetch_ohlcv("BTC/USDT", timeframe=tf, limit=100)
                df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
                close = df["close"]
                high = df["high"]
                low = df["low"]

                # EMA
                ema12 = float(close.ewm(span=12).mean().iloc[-1])
                ema26 = float(close.ewm(span=26).mean().iloc[-1])
                ema_gap_pct = abs(ema12 - ema26) / ema26 * 100 if ema26 else 0

                # RSI
                delta = close.diff()
                gain = delta.where(delta > 0, 0).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))

                # ATR
                tr = pd.concat([high - low, (high - close.shift(1)).abs(),
                                (low - close.shift(1)).abs()], axis=1).max(axis=1)
                atr = float(tr.rolling(14).mean().iloc[-1])

                # Volume Profile 근사: 가격대별 거래량 집중도
                price_range = high.tail(50).max() - low.tail(50).min()
                if price_range > 0:
                    bins = 20
                    price_bins = np.linspace(low.tail(50).min(), high.tail(50).max(), bins + 1)
                    vol_profile = np.zeros(bins)
                    for i in range(len(df.tail(50))):
                        row = df.iloc[-(50 - i)]
                        bin_idx = np.clip(int((row["close"] - price_bins[0]) / (price_range / bins)), 0, bins - 1)
                        vol_profile[bin_idx] += row["volume"]
                    poc_idx = int(np.argmax(vol_profile))
                    poc_price = float((price_bins[poc_idx] + price_bins[poc_idx + 1]) / 2)
                    # 지지/저항: 거래량 상위 3개 레벨
                    top_levels = np.argsort(vol_profile)[-3:][::-1]
                    vol_levels = [float((price_bins[i] + price_bins[i + 1]) / 2) for i in top_levels]
                else:
                    poc_price = float(close.iloc[-1])
                    vol_levels = []

                tail_count = 96 if tf == "15m" else 24 if tf == "1h" else 6
                data[f"btc_{tf}"] = {
                    "current": float(close.iloc[-1]),
                    "high_24h": float(high.tail(tail_count).max()),
                    "low_24h": float(low.tail(tail_count).min()),
                    "volume_avg": float(df["volume"].tail(20).mean()),
                    "volume_latest": float(df["volume"].iloc[-1]),
                    "volume_ratio": round(float(df["volume"].iloc[-1]) / max(float(df["volume"].tail(20).mean()), 1), 2),
                    "ema_12": ema12,
                    "ema_26": ema26,
                    "ema_gap_pct": round(ema_gap_pct, 3),
                    "rsi": round(float(rsi.iloc[-1]), 1),
                    "atr": round(atr, 2),
                    "atr_pct": round(atr / float(close.iloc[-1]) * 100, 3),
                    "poc_price": round(poc_price, 2),
                    "volume_levels": [round(v, 2) for v in vol_levels],
                }
            except Exception as e:
                data[f"btc_{tf}"] = {"error": str(e)}

        # 2. CoinGlass 데이터
        if self.cg:
            # OI 변화
            try:
                oi = self.cg.get_oi_change("BTC")
                data["oi"] = oi or {}
            except Exception:
                data["oi"] = {}

            # 펀딩레이트 트렌드
            try:
                funding = self.cg.get_funding_rate_trend("BTC")
                data["funding"] = funding or {}
            except Exception:
                data["funding"] = {}

            # 롱/숏 비율
            try:
                ls = self.cg.get_global_ls_ratio("BTC")
                data["ls_ratio"] = ls or {}
            except Exception:
                data["ls_ratio"] = {}

            # Taker 매수/매도 압력
            try:
                taker = self.cg.get_taker_pressure("BTC")
                data["taker"] = taker or {}
            except Exception:
                data["taker"] = {}

            # 청산 클러스터 (핵심!)
            try:
                btc_price = data.get("btc_4h", {}).get("current", 0)
                liq_clusters = self.cg.analyze_liquidation_clusters("BTC", btc_price)
                data["liq_clusters"] = liq_clusters or {}
            except Exception:
                data["liq_clusters"] = {}

            # 최근 청산 압력
            try:
                liq_pressure = self.cg.get_recent_liquidation_pressure("BTC")
                data["liq_pressure"] = liq_pressure or {}
            except Exception:
                data["liq_pressure"] = {}

            # 공포탐욕지수
            try:
                fgi = self.cg.get_fear_greed_index()
                data["fear_greed"] = fgi or {}
            except Exception:
                data["fear_greed"] = {}

        # 3. BTCFilter 상태 (기존 v9 분석 재활용)
        if self.btc_filter:
            try:
                ctx = self.btc_filter.analyze(fast=True)
                data["btc_filter"] = {
                    "market_state": ctx.market_state,
                    "market_score": ctx.market_score,
                    "long_safe": ctx.long_safe,
                    "short_safe": ctx.short_safe,
                    "long_boost": ctx.long_boost,
                    "short_boost": ctx.short_boost,
                    "danger_level": ctx.danger_level,
                    "mood_shift": ctx.mood_shift,
                    "reasons": ctx.reasons[:5],
                }
            except Exception:
                data["btc_filter"] = {}

        return data

    def analyze(self, collected_data: dict, context: dict | None = None) -> AgentMessage:
        """BTC 구조 분석 수행"""
        btc_4h = collected_data.get("btc_4h", {})
        btc_1h = collected_data.get("btc_1h", {})
        btc_15m = collected_data.get("btc_15m", {})
        oi = collected_data.get("oi", {})
        funding = collected_data.get("funding", {})
        ls_ratio = collected_data.get("ls_ratio", {})
        taker = collected_data.get("taker", {})
        liq_clusters = collected_data.get("liq_clusters", {})
        liq_pressure = collected_data.get("liq_pressure", {})
        fear_greed = collected_data.get("fear_greed", {})
        btc_filter = collected_data.get("btc_filter", {})

        # 청산 클러스터 정보 정리
        liq_info = ""
        if liq_clusters:
            nearest_long = liq_clusters.get("nearest_long_liq")
            nearest_short = liq_clusters.get("nearest_short_liq")
            if nearest_long:
                liq_info += (f"- 가장 가까운 롱 청산: ${nearest_long.get('volume_usd', 0)/1e6:.1f}M "
                             f"@{nearest_long.get('price', 0):,.0f} ({nearest_long.get('distance_pct', 0):.1f}% 아래)\n")
            if nearest_short:
                liq_info += (f"- 가장 가까운 숏 청산: ${nearest_short.get('volume_usd', 0)/1e6:.1f}M "
                             f"@{nearest_short.get('price', 0):,.0f} ({nearest_short.get('distance_pct', 0):.1f}% 위)\n")
            liq_info += (f"- 청산 중력 방향: {liq_clusters.get('gravity_direction', 'N/A')} "
                         f"(강도: {liq_clusters.get('gravity_strength', 0)}/10)\n")
            liq_info += (f"- 총 롱 청산 대기: ${liq_clusters.get('total_long_liq_usd', 0)/1e6:.1f}M | "
                         f"총 숏 청산 대기: ${liq_clusters.get('total_short_liq_usd', 0)/1e6:.1f}M")

        # BTCFilter 정보
        filter_info = ""
        if btc_filter:
            filter_info = (f"- 시장 상태: {btc_filter.get('market_state', 'N/A')} "
                           f"(점수: {btc_filter.get('market_score', 0):+d})\n"
                           f"- 롱 허용: {'✅' if btc_filter.get('long_safe') else '❌'} "
                           f"(부스트: {btc_filter.get('long_boost', 0):+d})\n"
                           f"- 숏 허용: {'✅' if btc_filter.get('short_safe') else '❌'} "
                           f"(부스트: {btc_filter.get('short_boost', 0):+d})\n"
                           f"- 분위기 변화: {btc_filter.get('mood_shift', 'STABLE')}\n"
                           f"- 근거: {', '.join(btc_filter.get('reasons', []))}")

        prompt = f"""다음 BTC 데이터를 분석하여 구조적 판단을 내려주세요.

## 가격 데이터 (멀티타임프레임)
### 4시간봉
- 현재가: ${btc_4h.get('current', 'N/A')}
- 24h 범위: ${btc_4h.get('low_24h', 'N/A')} ~ ${btc_4h.get('high_24h', 'N/A')}
- EMA 12/26: ${btc_4h.get('ema_12', 'N/A')} / ${btc_4h.get('ema_26', 'N/A')} (갭: {btc_4h.get('ema_gap_pct', 'N/A')}%)
- RSI: {btc_4h.get('rsi', 'N/A')} | ATR: {btc_4h.get('atr_pct', 'N/A')}%
- 거래량 비율(최근/평균): {btc_4h.get('volume_ratio', 'N/A')}x
- Volume POC: ${btc_4h.get('poc_price', 'N/A')}
- 매물대 레벨: {btc_4h.get('volume_levels', [])}

### 1시간봉
- EMA 12/26: ${btc_1h.get('ema_12', 'N/A')} / ${btc_1h.get('ema_26', 'N/A')}
- RSI: {btc_1h.get('rsi', 'N/A')} | 거래량 비율: {btc_1h.get('volume_ratio', 'N/A')}x

### 15분봉
- EMA 12/26: ${btc_15m.get('ema_12', 'N/A')} / ${btc_15m.get('ema_26', 'N/A')}
- RSI: {btc_15m.get('rsi', 'N/A')}

## 파생상품 데이터
- OI 변화: {oi.get('change_pct', 'N/A')}%, 트렌드: {oi.get('trend', 'N/A')}
- 펀딩레이트: {funding.get('rates_str', 'N/A')}, 시그널: {funding.get('signal', 'N/A')}
- 롱/숏 비율: 롱 {ls_ratio.get('long_pct', 'N/A')}% / 숏 {ls_ratio.get('short_pct', 'N/A')}%
- Taker 압력: {taker.get('signal', 'N/A')} (비율: {taker.get('sell_buy_ratio', 'N/A')})
- 최근 청산: {liq_pressure.get('dominant', 'N/A')} (롱${liq_pressure.get('long_liquidation_usd', 0)/1e6:.1f}M / 숏${liq_pressure.get('short_liquidation_usd', 0)/1e6:.1f}M)

## 🧲 청산 클러스터 (핵심!)
{liq_info or "데이터 없음"}

## BTCFilter 7단계 상태
{filter_info or "데이터 없음"}

## 공포탐욕지수
{fear_greed if isinstance(fear_greed, str) else f"값: {fear_greed}" if fear_greed else "N/A"}

## 20x 레버리지 핵심 고려사항
- ~4% 역행 = 청산
- 청산 클러스터가 가격 자석 역할 (큰 물량 쪽으로 끌려감)
- 청산 중력 방향이 포지션 반대면 매우 위험

주요 지지/저항 레벨을 식별하고, 청산 클러스터와 시장 데이터를 종합하여
상방 돌파 확률과 리스크 수준을 평가하세요."""

        result = self.llm_json(prompt, deep=True, max_tokens=2500)

        if result.get("parse_error"):
            result = self._fallback_analysis(collected_data)

        confidence = result.get("confidence", 0.6)
        warnings = []

        if result.get("risk_level") == "DANGER":
            warnings.append("⚠️ BTC 구조적 위험 감지")
        if liq_clusters.get("gravity_direction") == "DOWN" and liq_clusters.get("gravity_strength", 0) >= 6:
            warnings.append(f"🧲 청산 중력 하방 강도 {liq_clusters['gravity_strength']}/10")
        if btc_filter.get("danger_level", 0) >= 2:
            warnings.append(f"🔴 BTCFilter: {btc_filter.get('market_state', '')} (위험 {btc_filter['danger_level']})")
        if btc_filter.get("mood_shift") == "CRASHING":
            warnings.append("📉 BTC 분위기 급락 중")

        return self._build_message(
            data=result,
            confidence=confidence,
            reasoning=result.get("reasoning", ""),
            warnings=warnings
        )

    def _fallback_analysis(self, data: dict) -> dict:
        """LLM 파싱 실패 시 규칙 기반 폴백"""
        btc_4h = data.get("btc_4h", {})
        btc_filter = data.get("btc_filter", {})
        liq_clusters = data.get("liq_clusters", {})
        price = btc_4h.get("current", 0)
        ema12 = btc_4h.get("ema_12", 0)
        ema26 = btc_4h.get("ema_26", 0)

        # 트렌드 판단 (EMA + BTCFilter 종합)
        if ema12 > ema26:
            trend = "bullish"
            breakout_prob = 0.55
        elif ema12 < ema26:
            trend = "bearish"
            breakout_prob = 0.35
        else:
            trend = "neutral"
            breakout_prob = 0.45

        # BTCFilter 반영
        filter_state = btc_filter.get("market_state", "NEUTRAL")
        if filter_state in ("BEAR", "CRASH"):
            trend = "bearish"
            breakout_prob = max(breakout_prob - 0.15, 0.1)
        elif filter_state in ("BULL", "STRONG_BULL"):
            trend = "bullish"
            breakout_prob = min(breakout_prob + 0.1, 0.8)

        # 리스크 레벨 결정
        danger = btc_filter.get("danger_level", 0)
        gravity = liq_clusters.get("gravity_strength", 0)
        gravity_dir = liq_clusters.get("gravity_direction", "NEUTRAL")

        if danger >= 2 or (gravity >= 7 and gravity_dir == "DOWN"):
            risk_level = "DANGER"
        elif danger >= 1 or gravity >= 5:
            risk_level = "CAUTION"
        else:
            risk_level = "SAFE"

        return {
            "btc_price": price,
            "key_resistance": btc_4h.get("volume_levels", []),
            "key_support": [],
            "volume_profile_poc": btc_4h.get("poc_price", 0),
            "breakout_probability": round(breakout_prob, 2),
            "trend": trend,
            "liquidation_gravity": gravity_dir,
            "risk_level": risk_level,
            "confidence": 0.5,
            "reasoning": (f"규칙 기반 폴백: {filter_state}, "
                          f"청산중력 {gravity_dir}({gravity}/10), "
                          f"위험도 {danger}")
        }
