"""
alt_structure.py — 알트코인 구조 분석가
========================================
임의의 알트코인 가격 구조, 핵심 레벨 분석.
BTCStructureAgent 패턴 기반, 심볼 파라미터화.
분석 유형: Factual (사실 기반)

데이터 소스:
- Binance API (가격, 거래량, 멀티타임프레임)
- Volume Profile (HVN/LVN/POC) + 돌파/반동 패턴
"""

from __future__ import annotations
import pandas as pd
import numpy as np

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage
from agents.output.chart_generator import generate_alt_chart


class AltStructureAgent(AgentBase):
    """
    알트코인 구조 분석가

    책임:
    - 지정된 알트코인의 주요 지지/저항 레벨 식별 (Volume Profile)
    - 돌파/반동 패턴 감지
    - EMA/RSI/ATR 멀티타임프레임 분석
    - 추세 및 돌파 확률 추정
    """

    def __init__(self, symbol: str = "SOL/USDT", exchange=None):
        coin = symbol.replace("/USDT", "").replace("USDT", "").upper()
        super().__init__(
            agent_id=f"agent_alt_{coin.lower()}",
            agent_name="알트코인 분석가",
            role_description=f"{coin} 가격 구조와 핵심 레벨을 분석하는 Factual 에이전트"
        )
        self._analysis_type = "factual"
        self.symbol = f"{coin}/USDT"
        self.coin = coin
        if exchange is None:
            import ccxt
            exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        self.exchange = exchange

    def _get_system_prompt(self) -> str:
        return f"""당신은 {self.coin} 가격 구조 전문 분석가입니다.
20배 레버리지 선물 트레이딩 환경에서 {self.coin}의 핵심 가격 레벨을 분석합니다.

분석 원칙:
- 사실적(Factual) 데이터에 기반한 객관적 분석
- 매물대(Volume Profile)에서 HVN = 지지/저항, LVN = 돌파 가능 구간
- 돌파 확률은 보수적으로 추정 (생존 우선)
- 20x 레버리지 → ~4% 역행 = 청산. 리스크 항상 반영

롱/숏 추천 진입 가이드 원칙:
- 롱 추천: 지지 매물대(HVN) 근처 또는 VA 하단 근처에서 반등 기대
- 숏 추천: 저항 매물대(HVN) 근처 또는 VA 상단 근처에서 하락 기대
- SL은 진입가 기준 20x 레버리지에서 -60~80% 손실 이내 (가격 기준 ~3~4%)
- TP는 다음 핵심 매물대 또는 반대쪽 VA 경계

반드시 JSON 형식으로 출력하세요:
```json
{{
  "coin": "{self.coin}",
  "current_price": 현재가,
  "key_resistance": [저항 레벨들],
  "key_support": [지지 레벨들],
  "volume_profile_poc": 거래량 집중 가격대(POC),
  "value_area": {{"low": VA하단, "high": VA상단}},
  "breakout_probability": 상방돌파확률(0~1),
  "trend": "bullish|neutral|bearish",
  "trend_strength": "strong|moderate|weak",
  "patterns_detected": ["감지된 패턴들"],
  "long_entry": {{
    "entry_price": 롱추천진입가,
    "stop_loss": 롱손절가,
    "take_profit": 롱목표가,
    "reason": "진입 근거 (한국어)"
  }},
  "short_entry": {{
    "entry_price": 숏추천진입가,
    "stop_loss": 숏손절가,
    "take_profit": 숏목표가,
    "reason": "진입 근거 (한국어)"
  }},
  "risk_level": "SAFE|CAUTION|DANGER",
  "confidence": 0.0~1.0,
  "reasoning": "종합 분석 근거 (한국어)"
}}
```"""

    @staticmethod
    def _compute_volume_profile(df: pd.DataFrame, lookback: int = 50, bins: int = 30) -> dict:
        """Volume Profile 근사 계산 (BTCStructureAgent와 동일 로직)"""
        tail = df.tail(lookback)
        price_range = tail["high"].max() - tail["low"].min()
        if price_range <= 0:
            return {"poc": float(df["close"].iloc[-1]), "hvn": [], "lvn": [], "value_area": {}}

        price_bins = np.linspace(tail["low"].min(), tail["high"].max(), bins + 1)
        vol_profile = np.zeros(bins)

        for _, row in tail.iterrows():
            low_bin = np.clip(int((row["low"] - price_bins[0]) / (price_range / bins)), 0, bins - 1)
            high_bin = np.clip(int((row["high"] - price_bins[0]) / (price_range / bins)), 0, bins - 1)
            if low_bin == high_bin:
                vol_profile[low_bin] += row["volume"]
            else:
                spread = high_bin - low_bin + 1
                for b in range(low_bin, high_bin + 1):
                    vol_profile[b] += row["volume"] / spread

        poc_idx = int(np.argmax(vol_profile))
        poc_price = float((price_bins[poc_idx] + price_bins[poc_idx + 1]) / 2)

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

        threshold_high = np.percentile(vol_profile, 80)
        hvn = []
        for i in range(bins):
            if vol_profile[i] >= threshold_high:
                hvn.append(round(float((price_bins[i] + price_bins[i + 1]) / 2), 4))

        threshold_low = np.percentile(vol_profile[vol_profile > 0], 20) if vol_profile.sum() > 0 else 0
        lvn = []
        for i in range(bins):
            if 0 < vol_profile[i] <= threshold_low:
                lvn.append(round(float((price_bins[i] + price_bins[i + 1]) / 2), 4))

        return {
            "poc": round(poc_price, 4),
            "hvn": hvn[:5],
            "lvn": lvn[:5],
            "value_area": {"low": round(va_low, 4), "high": round(va_high, 4)},
        }

    @staticmethod
    def _detect_breakout_patterns(df: pd.DataFrame, vol_profile: dict) -> dict:
        """가격 행동 기반 돌파/반동 패턴 감지"""
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

        for lvl in hvn_levels:
            if float(high.iloc[-1]) >= lvl > current and (float(high.iloc[-1]) - current) > (current - float(low.iloc[-1])):
                patterns.append({
                    "type": "rejection_resistance",
                    "level": lvl,
                    "desc": f"${lvl:,.4f} 저항 매물대에서 반동(위꼬리)"
                })
            if float(low.iloc[-1]) <= lvl < current and (current - float(low.iloc[-1])) > (float(high.iloc[-1]) - current):
                patterns.append({
                    "type": "rejection_support",
                    "level": lvl,
                    "desc": f"${lvl:,.4f} 지지 매물대에서 반동(아래꼬리)"
                })

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

        last_3 = close.tail(4).values
        if len(last_3) == 4:
            if all(last_3[i] > last_3[i-1] for i in range(1, 4)):
                patterns.append({"type": "momentum_up", "candles": 3, "desc": "3연속 양봉"})
            elif all(last_3[i] < last_3[i-1] for i in range(1, 4)):
                patterns.append({"type": "momentum_down", "candles": 3, "desc": "3연속 음봉"})

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
        """알트코인 멀티타임프레임 데이터 수집"""
        data = {"coin": self.coin, "symbol": self.symbol}

        for tf in ["15m", "1h", "4h"]:
            try:
                ohlcv = self.exchange.fetch_ohlcv(self.symbol, timeframe=tf, limit=100)
                df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
                if tf == "4h":
                    data["_ohlcv_4h"] = df
                close = df["close"]
                high = df["high"]
                low = df["low"]

                ema12 = float(close.ewm(span=12).mean().iloc[-1])
                ema26 = float(close.ewm(span=26).mean().iloc[-1])
                ema_gap_pct = abs(ema12 - ema26) / ema26 * 100 if ema26 else 0

                delta = close.diff()
                gain = delta.where(delta > 0, 0).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rsi = 100 - (100 / (1 + gain / (loss + 1e-9)))

                tr = pd.concat([high - low, (high - close.shift(1)).abs(),
                                (low - close.shift(1)).abs()], axis=1).max(axis=1)
                atr = float(tr.rolling(14).mean().iloc[-1])

                vol_profile = self._compute_volume_profile(df, lookback=50, bins=30)

                bp = {}
                if tf in ("4h", "1h"):
                    bp = self._detect_breakout_patterns(df, vol_profile)

                tail_count = 96 if tf == "15m" else 24 if tf == "1h" else 6
                data[f"{tf}"] = {
                    "current": float(close.iloc[-1]),
                    "high_24h": float(high.tail(tail_count).max()),
                    "low_24h": float(low.tail(tail_count).min()),
                    "change_24h_pct": round((float(close.iloc[-1]) / float(close.iloc[-tail_count]) - 1) * 100, 2) if len(close) > tail_count else 0,
                    "volume_avg": float(df["volume"].tail(20).mean()),
                    "volume_latest": float(df["volume"].iloc[-1]),
                    "volume_ratio": round(float(df["volume"].iloc[-1]) / max(float(df["volume"].tail(20).mean()), 1), 2),
                    "ema_12": round(ema12, 4),
                    "ema_26": round(ema26, 4),
                    "ema_gap_pct": round(ema_gap_pct, 3),
                    "rsi": round(float(rsi.iloc[-1]), 1),
                    "atr": round(atr, 4),
                    "atr_pct": round(atr / float(close.iloc[-1]) * 100, 3),
                    "volume_profile": vol_profile,
                    "breakout_patterns": bp,
                }
            except Exception as e:
                data[f"{tf}"] = {"error": str(e)}

        return data

    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
        """알트코인 구조 분석 수행"""
        coin = collected_data.get("coin", self.coin)
        d4h = collected_data.get("4h", {})
        d1h = collected_data.get("1h", {})
        d15m = collected_data.get("15m", {})

        vp_4h = d4h.get("volume_profile", {})
        bp_4h = d4h.get("breakout_patterns", {})
        bp_1h = d1h.get("breakout_patterns", {})
        va_4h = vp_4h.get("value_area", {})

        vp_info = ""
        if vp_4h:
            vp_info = (f"- POC (최대 거래량): {vp_4h.get('poc', 'N/A')}\n"
                       f"- Value Area: {va_4h.get('low', 'N/A')} ~ {va_4h.get('high', 'N/A')}\n"
                       f"- HVN (지지/저항 매물대): {vp_4h.get('hvn', [])}\n"
                       f"- LVN (돌파 가능 구간): {vp_4h.get('lvn', [])}")

        pattern_info = ""
        for label, bp_data in [("4h", bp_4h), ("1h", bp_1h)]:
            if bp_data and bp_data.get("patterns"):
                pattern_info += f"\n### {label} 패턴 (편향: {bp_data.get('pattern_bias', 'N/A')})\n"
                for p in bp_data.get("patterns", []):
                    pattern_info += f"  - {p.get('desc', p.get('type', ''))}\n"

        prompt = f"""다음 {coin} 데이터를 분석하여 구조적 판단을 내려주세요.

## 가격 데이터 (멀티타임프레임)
### 4시간봉
- 현재가: {d4h.get('current', 'N/A')}
- 24h 범위: {d4h.get('low_24h', 'N/A')} ~ {d4h.get('high_24h', 'N/A')}
- 24h 변동: {d4h.get('change_24h_pct', 'N/A')}%
- EMA 12/26: {d4h.get('ema_12', 'N/A')} / {d4h.get('ema_26', 'N/A')} (갭: {d4h.get('ema_gap_pct', 'N/A')}%)
- RSI: {d4h.get('rsi', 'N/A')} | ATR: {d4h.get('atr_pct', 'N/A')}%
- 거래량 비율(최근/평균): {d4h.get('volume_ratio', 'N/A')}x

### 매물대 분석 (Volume Profile)
{vp_info or "데이터 없음"}

### 돌파/반동 패턴
{pattern_info or "감지된 패턴 없음"}

### 1시간봉
- EMA 12/26: {d1h.get('ema_12', 'N/A')} / {d1h.get('ema_26', 'N/A')} (갭: {d1h.get('ema_gap_pct', 'N/A')}%)
- RSI: {d1h.get('rsi', 'N/A')} | ATR: {d1h.get('atr_pct', 'N/A')}%
- 거래량 비율: {d1h.get('volume_ratio', 'N/A')}x

### 15분봉
- EMA 12/26: {d15m.get('ema_12', 'N/A')} / {d15m.get('ema_26', 'N/A')}
- RSI: {d15m.get('rsi', 'N/A')} | ATR: {d15m.get('atr_pct', 'N/A')}%

## 20x 레버리지 핵심 고려사항
- ~4% 역행 = 청산
- 매물대(HVN) 근처에서는 반동 가능성 높음
- LVN 구간은 가격이 빠르게 이동 → 돌파 시 가속

주요 지지/저항 레벨을 식별하고, 매물대와 패턴을 종합하여
상방 돌파 확률과 리스크 수준을 평가하세요.

또한 롱/숏 각각의 입장에서 최적의 매수 추천 가격을 제시하세요:
- 롱 진입: 어디서 매수하면 좋은지 (지지 매물대, VA 하단 등 근거)
- 숏 진입: 어디서 매도하면 좋은지 (저항 매물대, VA 상단 등 근거)
- 각각의 SL(손절)과 TP(목표) 가격도 함께 제시
- SL은 20x 레버리지에서 버틸 수 있는 범위 내 (~3~4% 이내)

{coin}의 현재 가격 구조를 한국어로 명확하게 설명해주세요."""

        result = self.llm_json(prompt, deep=True, max_tokens=2500)

        if result.get("parse_error"):
            result = self._fallback_analysis(collected_data)

        confidence = result.get("confidence", 0.6)
        warnings = []

        if result.get("risk_level") == "DANGER":
            warnings.append(f"⚠️ {coin} 구조적 위험 감지")

        rsi_4h = d4h.get("rsi", 50)
        if isinstance(rsi_4h, (int, float)):
            if rsi_4h > 75:
                warnings.append(f"🔴 {coin} 4h RSI {rsi_4h:.0f} — 과매수")
            elif rsi_4h < 25:
                warnings.append(f"🟢 {coin} 4h RSI {rsi_4h:.0f} — 과매도")

        return self._build_message(
            data=result,
            confidence=confidence,
            reasoning=result.get("reasoning", ""),
            warnings=warnings
        )

    def _fallback_analysis(self, data: dict) -> dict:
        """LLM 파싱 실패 시 규칙 기반 폴백"""
        d4h = data.get("4h", {})
        price = d4h.get("current", 0)
        ema12 = d4h.get("ema_12", 0)
        ema26 = d4h.get("ema_26", 0)

        if ema12 > ema26:
            trend = "bullish"
            breakout_prob = 0.55
        elif ema12 < ema26:
            trend = "bearish"
            breakout_prob = 0.35
        else:
            trend = "neutral"
            breakout_prob = 0.45

        bp = d4h.get("breakout_patterns", {})
        pattern_bias = bp.get("pattern_bias", "neutral")
        if pattern_bias == "bullish":
            breakout_prob = min(breakout_prob + 0.1, 0.85)
        elif pattern_bias == "bearish":
            breakout_prob = max(breakout_prob - 0.1, 0.1)

        rsi = d4h.get("rsi", 50)
        if isinstance(rsi, (int, float)):
            if rsi > 70:
                risk_level = "CAUTION"
            elif rsi < 30:
                risk_level = "CAUTION"
            else:
                risk_level = "SAFE"
        else:
            risk_level = "SAFE"

        vp = d4h.get("volume_profile", {})
        va = vp.get("value_area", {})

        resistances = [lvl for lvl in vp.get("hvn", []) if lvl > price]
        support_lvls = [lvl for lvl in vp.get("hvn", []) if lvl < price]
        va_low = va.get("low", price * 0.97)
        va_high = va.get("high", price * 1.03)
        atr = d4h.get("atr", price * 0.02)

        long_entry_price = max(support_lvls) if support_lvls else va_low
        long_sl = long_entry_price - atr * 1.5
        long_tp = resistances[0] if resistances else va_high

        short_entry_price = min(resistances) if resistances else va_high
        short_sl = short_entry_price + atr * 1.5
        short_tp = support_lvls[-1] if support_lvls else va_low

        return {
            "coin": self.coin,
            "current_price": price,
            "key_resistance": resistances,
            "key_support": support_lvls,
            "volume_profile_poc": vp.get("poc", 0),
            "value_area": va,
            "breakout_probability": round(breakout_prob, 2),
            "trend": trend,
            "trend_strength": "moderate",
            "patterns_detected": [p.get("desc", "") for p in bp.get("patterns", [])],
            "long_entry": {
                "entry_price": round(long_entry_price, 4),
                "stop_loss": round(long_sl, 4),
                "take_profit": round(long_tp, 4),
                "reason": f"지지 매물대 근처 반등 기대"
            },
            "short_entry": {
                "entry_price": round(short_entry_price, 4),
                "stop_loss": round(short_sl, 4),
                "take_profit": round(short_tp, 4),
                "reason": f"저항 매물대 근처 하락 기대"
            },
            "risk_level": risk_level,
            "confidence": 0.5,
            "reasoning": f"규칙 기반 폴백: EMA {trend}, 패턴 {pattern_bias}, RSI {rsi}"
        }

    def generate_chart(self, collected_data: dict, analysis_msg: AgentMessage) -> bytes:
        """분석 결과를 차트 이미지(PNG bytes)로 생성"""
        ohlcv_df = collected_data.get("_ohlcv_4h", pd.DataFrame())
        return generate_alt_chart(
            coin=self.coin,
            ohlcv_df=ohlcv_df,
            analysis_result=analysis_msg.data,
            collected_data=collected_data,
        )

    def format_telegram(self, msg: AgentMessage) -> str:
        """분석 결과를 텔레그램 메시지로 포맷팅"""
        d = msg.data
        coin = d.get("coin", self.coin)
        price = d.get("current_price", 0)
        trend = d.get("trend", "N/A")
        strength = d.get("trend_strength", "N/A")
        prob = d.get("breakout_probability", 0)
        risk = d.get("risk_level", "N/A")
        conf = d.get("confidence", 0)

        trend_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(trend, "⚪")
        risk_emoji = {"SAFE": "🟢", "CAUTION": "🟡", "DANGER": "🔴"}.get(risk, "⚪")

        lines = [
            f"📊 {coin} 구조 분석",
            f"{'━' * 22}",
            f"현재가: {price}",
            f"추세: {trend_emoji} {trend} ({strength})",
            f"상방돌파 확률: {prob:.0%}",
            f"리스크: {risk_emoji} {risk}",
            f"신뢰도: {conf:.0%}",
        ]

        supports = d.get("key_support", [])
        resistances = d.get("key_resistance", [])
        poc = d.get("volume_profile_poc", 0)
        va = d.get("value_area", {})

        if poc:
            lines.append(f"\n📍 POC: {poc}")
        if va:
            lines.append(f"📍 VA: {va.get('low', 'N/A')} ~ {va.get('high', 'N/A')}")
        if supports:
            lines.append(f"🟢 지지: {', '.join(str(s) for s in supports[:3])}")
        if resistances:
            lines.append(f"🔴 저항: {', '.join(str(r) for r in resistances[:3])}")

        # 롱/숏 추천 진입
        long_e = d.get("long_entry", {})
        short_e = d.get("short_entry", {})

        if long_e and long_e.get("entry_price"):
            le = long_e["entry_price"]
            lsl = long_e.get("stop_loss", 0)
            ltp = long_e.get("take_profit", 0)
            lines.append(f"\n🟢 LONG 추천")
            lines.append(f"  진입: {le}")
            if lsl:
                sl_pnl = ((lsl - le) / le) * 20 * 100
                lines.append(f"  SL: {lsl} ({sl_pnl:+.1f}%)")
            if ltp:
                tp_pnl = ((ltp - le) / le) * 20 * 100
                lines.append(f"  TP: {ltp} (+{tp_pnl:.1f}%)")
            if long_e.get("reason"):
                lines.append(f"  → {long_e['reason']}")

        if short_e and short_e.get("entry_price"):
            se = short_e["entry_price"]
            ssl = short_e.get("stop_loss", 0)
            stp = short_e.get("take_profit", 0)
            lines.append(f"\n🔴 SHORT 추천")
            lines.append(f"  진입: {se}")
            if ssl:
                sl_pnl = ((se - ssl) / se) * 20 * 100
                lines.append(f"  SL: {ssl} ({sl_pnl:+.1f}%)")
            if stp:
                tp_pnl = ((se - stp) / se) * 20 * 100
                lines.append(f"  TP: {stp} (+{tp_pnl:.1f}%)")
            if short_e.get("reason"):
                lines.append(f"  → {short_e['reason']}")

        patterns = d.get("patterns_detected", [])
        if patterns:
            lines.append(f"\n🔍 패턴:")
            for p in patterns[:3]:
                lines.append(f"  • {p}")

        if msg.warnings:
            lines.append("")
            for w in msg.warnings:
                lines.append(w)

        reasoning = d.get("reasoning", "")
        if reasoning:
            lines.append(f"\n💡 {reasoning[:300]}")

        return "\n".join(lines)
