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

    # ── 매물대(Volume Profile) 분석 ──

    @staticmethod
    def _compute_volume_profile(df: pd.DataFrame, lookback: int = 50, bins: int = 30) -> dict:
        """
        Volume Profile 근사 계산.
        HVN(High Volume Node) = 지지/저항, LVN(Low Volume Node) = 돌파 가능 구간.
        """
        tail = df.tail(lookback)
        price_range = tail["high"].max() - tail["low"].min()
        if price_range <= 0:
            return {"poc": float(df["close"].iloc[-1]), "hvn": [], "lvn": [], "value_area": {}}

        price_bins = np.linspace(tail["low"].min(), tail["high"].max(), bins + 1)
        vol_profile = np.zeros(bins)

        for _, row in tail.iterrows():
            # 캔들 전체 범위에 거래량 분배 (더 정확한 근사)
            low_bin = np.clip(int((row["low"] - price_bins[0]) / (price_range / bins)), 0, bins - 1)
            high_bin = np.clip(int((row["high"] - price_bins[0]) / (price_range / bins)), 0, bins - 1)
            if low_bin == high_bin:
                vol_profile[low_bin] += row["volume"]
            else:
                spread = high_bin - low_bin + 1
                for b in range(low_bin, high_bin + 1):
                    vol_profile[b] += row["volume"] / spread

        # POC (Point of Control)
        poc_idx = int(np.argmax(vol_profile))
        poc_price = float((price_bins[poc_idx] + price_bins[poc_idx + 1]) / 2)

        # Value Area (거래량 70% 집중 구간)
        total_vol = vol_profile.sum()
        sorted_bins = np.argsort(vol_profile)[::-1]
        cumulative = 0.0
        va_bins = set()
        for b in sorted_bins:
            va_bins.add(b)
            cumulative += vol_profile[b]
            if cumulative >= total_vol * 0.7:
                break
        va_low = float(price_bins[min(va_bins)])
        va_high = float(price_bins[max(va_bins) + 1])

        # HVN: 상위 20% 거래량 구간 (강한 지지/저항)
        threshold_high = np.percentile(vol_profile, 80)
        hvn = []
        for i in range(bins):
            if vol_profile[i] >= threshold_high:
                hvn.append(round(float((price_bins[i] + price_bins[i + 1]) / 2), 2))

        # LVN: 하위 20% 거래량 구간 (가격 빠르게 통과 = 돌파 가능)
        threshold_low = np.percentile(vol_profile[vol_profile > 0], 20) if vol_profile.sum() > 0 else 0
        lvn = []
        for i in range(bins):
            if 0 < vol_profile[i] <= threshold_low:
                lvn.append(round(float((price_bins[i] + price_bins[i + 1]) / 2), 2))

        return {
            "poc": round(poc_price, 2),
            "hvn": hvn[:5],  # 상위 5개
            "lvn": lvn[:5],
            "value_area": {"low": round(va_low, 2), "high": round(va_high, 2)},
        }

    # ── 돌파/반동 패턴 인식 ──

    @staticmethod
    def _detect_breakout_patterns(df: pd.DataFrame, vol_profile: dict) -> dict:
        """
        가격 행동 기반 돌파/반동 패턴 감지.
        - 매물대(HVN) 돌파: 거래량 동반 여부로 진위 판단
        - 반동(Rejection): HVN에서 꼬리 형성
        - Squeeze → Expansion: 변동성 수축 후 확장
        """
        if len(df) < 20:
            return {"pattern": "insufficient_data"}

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]
        current = float(close.iloc[-1])
        avg_vol = float(volume.tail(20).mean())

        patterns = []
        hvn_levels = vol_profile.get("hvn", [])
        va = vol_profile.get("value_area", {})
        va_high = va.get("high", current * 1.05)
        va_low = va.get("low", current * 0.95)

        # 1. Value Area 돌파 감지
        prev_close = float(close.iloc[-2])
        vol_ratio = float(volume.iloc[-1]) / max(avg_vol, 1)

        if prev_close < va_high <= current:
            strength = "strong" if vol_ratio > 1.5 else "weak"
            patterns.append({
                "type": "va_breakout_up",
                "level": va_high,
                "vol_ratio": round(vol_ratio, 2),
                "strength": strength,
                "desc": f"VA 상단 돌파 ({strength}, vol {vol_ratio:.1f}x)"
            })
        elif prev_close > va_low >= current:
            strength = "strong" if vol_ratio > 1.5 else "weak"
            patterns.append({
                "type": "va_breakdown",
                "level": va_low,
                "vol_ratio": round(vol_ratio, 2),
                "strength": strength,
                "desc": f"VA 하단 이탈 ({strength}, vol {vol_ratio:.1f}x)"
            })

        # 2. HVN 반동(Rejection) 감지 — 꼬리가 HVN에 닿고 복귀
        for lvl in hvn_levels:
            # 위꼬리: high가 HVN 돌파했지만 close가 아래로 복귀
            if float(high.iloc[-1]) >= lvl > current and (float(high.iloc[-1]) - current) > (current - float(low.iloc[-1])):
                patterns.append({
                    "type": "rejection_resistance",
                    "level": lvl,
                    "desc": f"${lvl:,.0f} 저항 매물대에서 반동(위꼬리)"
                })
            # 아래꼬리: low가 HVN 하회했지만 close가 위로 복귀
            if float(low.iloc[-1]) <= lvl < current and (current - float(low.iloc[-1])) > (float(high.iloc[-1]) - current):
                patterns.append({
                    "type": "rejection_support",
                    "level": lvl,
                    "desc": f"${lvl:,.0f} 지지 매물대에서 반동(아래꼬리)"
                })

        # 3. Squeeze → Expansion (볼린저밴드 폭 기반)
        bb_width = ((high - low) / close * 100).rolling(20).mean()
        if len(bb_width.dropna()) >= 5:
            recent_width = float(bb_width.iloc[-1])
            avg_width = float(bb_width.tail(20).mean())
            min_width = float(bb_width.tail(20).min())

            if recent_width > avg_width * 1.5 and float(bb_width.iloc[-2]) < avg_width:
                direction = "up" if current > float(close.iloc[-2]) else "down"
                patterns.append({
                    "type": "squeeze_expansion",
                    "direction": direction,
                    "expansion_ratio": round(recent_width / max(min_width, 0.01), 2),
                    "desc": f"변동성 확장 ({direction}, {recent_width/max(min_width,0.01):.1f}x)"
                })

        # 4. 연속 방향 캔들 (모멘텀)
        last_3 = close.tail(4).values
        if len(last_3) == 4:
            if all(last_3[i] > last_3[i-1] for i in range(1, 4)):
                patterns.append({"type": "momentum_up", "candles": 3, "desc": "3연속 양봉"})
            elif all(last_3[i] < last_3[i-1] for i in range(1, 4)):
                patterns.append({"type": "momentum_down", "candles": 3, "desc": "3연속 음봉"})

        # 종합 판단
        bullish_patterns = sum(1 for p in patterns if p["type"] in
                               ("va_breakout_up", "rejection_support", "momentum_up")
                               or (p["type"] == "squeeze_expansion" and p.get("direction") == "up"))
        bearish_patterns = sum(1 for p in patterns if p["type"] in
                                ("va_breakdown", "rejection_resistance", "momentum_down")
                                or (p["type"] == "squeeze_expansion" and p.get("direction") == "down"))

        if bullish_patterns > bearish_patterns:
            bias = "bullish"
        elif bearish_patterns > bullish_patterns:
            bias = "bearish"
        else:
            bias = "neutral"

        return {
            "patterns": patterns[:5],
            "pattern_bias": bias,
            "bullish_count": bullish_patterns,
            "bearish_count": bearish_patterns,
        }

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

                # Volume Profile (HVN/LVN/VA 포함)
                vol_profile = self._compute_volume_profile(df, lookback=50, bins=30)

                # 돌파/반동 패턴 (4h, 1h에서만)
                bp = {}
                if tf in ("4h", "1h"):
                    bp = self._detect_breakout_patterns(df, vol_profile)

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
                    "poc_price": vol_profile["poc"],
                    "volume_levels": vol_profile["hvn"],
                    "volume_profile": vol_profile,
                    "breakout_patterns": bp,
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

    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
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

        # 매물대 & 패턴 정보
        vp_4h = btc_4h.get("volume_profile", {})
        bp_4h = btc_4h.get("breakout_patterns", {})
        bp_1h = btc_1h.get("breakout_patterns", {})
        va_4h = vp_4h.get("value_area", {})

        vp_info = ""
        if vp_4h:
            vp_info = (f"- POC (최대 거래량): ${vp_4h.get('poc', 'N/A')}\n"
                       f"- Value Area: ${va_4h.get('low', 'N/A')} ~ ${va_4h.get('high', 'N/A')}\n"
                       f"- HVN (지지/저항 매물대): {vp_4h.get('hvn', [])}\n"
                       f"- LVN (돌파 가능 구간): {vp_4h.get('lvn', [])}")

        pattern_info = ""
        for label, bp_data in [("4h", bp_4h), ("1h", bp_1h)]:
            if bp_data and bp_data.get("patterns"):
                pattern_info += f"\n### {label} 패턴 (편향: {bp_data.get('pattern_bias', 'N/A')})\n"
                for p in bp_data.get("patterns", []):
                    pattern_info += f"  - {p.get('desc', p.get('type', ''))}\n"

        prompt = f"""다음 BTC 데이터를 분석하여 구조적 판단을 내려주세요.

## 가격 데이터 (멀티타임프레임)
### 4시간봉
- 현재가: ${btc_4h.get('current', 'N/A')}
- 24h 범위: ${btc_4h.get('low_24h', 'N/A')} ~ ${btc_4h.get('high_24h', 'N/A')}
- EMA 12/26: ${btc_4h.get('ema_12', 'N/A')} / ${btc_4h.get('ema_26', 'N/A')} (갭: {btc_4h.get('ema_gap_pct', 'N/A')}%)
- RSI: {btc_4h.get('rsi', 'N/A')} | ATR: {btc_4h.get('atr_pct', 'N/A')}%
- 거래량 비율(최근/평균): {btc_4h.get('volume_ratio', 'N/A')}x

### 매물대 분석 (Volume Profile)
{vp_info or "데이터 없음"}

### 돌파/반동 패턴
{pattern_info or "감지된 패턴 없음"}

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

        # 트렌드 판단 (EMA + BTCFilter + 패턴 종합)
        if ema12 > ema26:
            trend = "bullish"
            breakout_prob = 0.55
        elif ema12 < ema26:
            trend = "bearish"
            breakout_prob = 0.35
        else:
            trend = "neutral"
            breakout_prob = 0.45

        # 패턴 편향 반영
        bp = btc_4h.get("breakout_patterns", {})
        pattern_bias = bp.get("pattern_bias", "neutral")
        if pattern_bias == "bullish":
            breakout_prob = min(breakout_prob + 0.1, 0.85)
        elif pattern_bias == "bearish":
            breakout_prob = max(breakout_prob - 0.1, 0.1)

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

        # Volume Profile 정보 통합
        vp = btc_4h.get("volume_profile", {})
        va = vp.get("value_area", {})

        return {
            "btc_price": price,
            "key_resistance": vp.get("hvn", btc_4h.get("volume_levels", [])),
            "key_support": [lvl for lvl in vp.get("hvn", []) if lvl < price],
            "volume_profile_poc": vp.get("poc", btc_4h.get("poc_price", 0)),
            "value_area": va,
            "breakout_probability": round(breakout_prob, 2),
            "trend": trend,
            "trend_strength": "moderate",
            "pattern_bias": pattern_bias,
            "patterns_detected": [p.get("desc", "") for p in bp.get("patterns", [])],
            "liquidation_gravity": gravity_dir,
            "risk_level": risk_level,
            "confidence": 0.5,
            "reasoning": (f"규칙 기반 폴백: {filter_state}, "
                          f"패턴 {pattern_bias}, "
                          f"청산중력 {gravity_dir}({gravity}/10), "
                          f"위험도 {danger}")
        }
