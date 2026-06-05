"""
breakout_scanner.py — 돌파 전문가 에이전트
============================================
4시간마다 워치리스트를 스캔하여 다음 조건의 알트코인을 탐색:
- LVN(Low Volume Node) 구간에서 횡보 중
- BTC는 하방 압력 (BEAR/CRASH)
- 알트가 상대적 강세 유지 → 돌파 후보

핵심 로직:
1. BTC 트렌드 확인 (하방 여부)
2. 알트별 Volume Profile → 현재가가 LVN에 위치하는지
3. 횡보 감지 (ATR 수축 + 좁은 레인지)
4. BTC 대비 상대강도 (BTC 하락 vs 알트 횡보/상승)
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from typing import Any

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage


class BreakoutScannerAgent(AgentBase):
    """
    돌파 전문가

    BTC 하방 시 LVN에서 횡보하며 버티는 강세 알트를 탐색.
    이런 코인은 BTC 반등 시 강한 돌파가 예상됨.
    """

    def __init__(self, watchlist: list[str] = None, exchange=None, btc_trend=None):
        super().__init__(
            agent_id="agent_breakout_scanner",
            agent_name="돌파 전문가",
            role_description="BTC 하방 시 LVN 횡보 강세 알트를 탐색하는 에이전트"
        )
        self._analysis_type = "factual"
        self.watchlist = watchlist or []
        if exchange is None:
            import ccxt
            exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        self.exchange = exchange
        self.btc_trend = btc_trend

    def _get_system_prompt(self) -> str:
        return """당신은 알트코인 돌파 전문 분석가입니다.
BTC가 하락하는 와중에도 LVN(Low Volume Node, 거래량 희박 구간)에서
횡보하며 버티는 강세 알트코인을 분석합니다.

이런 코인의 특성:
- LVN에서 횡보 = 매도 압력 약함, 스마트머니 축적 가능성
- BTC 하방인데 알트가 버팀 = 상대적 강세
- BTC 반등 시 LVN 돌파로 급등 가능 (LVN은 가격이 빠르게 통과하는 구간)

20x 레버리지 환경이므로 리스크를 항상 반영하세요.

반드시 JSON 형식으로 출력하세요:
```json
{
  "candidates": [
    {
      "coin": "코인명",
      "current_price": 현재가,
      "lvn_range": {"low": LVN하단, "high": LVN상단},
      "sideways_score": 횡보강도(0~1),
      "relative_strength": BTC대비상대강도(-1~1),
      "breakout_direction": "up|down",
      "breakout_target": 돌파시목표가,
      "confidence": 0.0~1.0,
      "reason": "분석 근거 (한국어)"
    }
  ],
  "btc_status": "BTC 현재 상태 요약",
  "scan_summary": "전체 스캔 요약 (한국어)"
}
```"""

    @staticmethod
    def _compute_volume_profile(df: pd.DataFrame, lookback: int = 50, bins: int = 30) -> dict:
        """Volume Profile 계산 → HVN/LVN/POC"""
        tail = df.tail(lookback)
        price_range = tail["high"].max() - tail["low"].min()
        if price_range <= 0:
            return {"poc": float(df["close"].iloc[-1]), "hvn": [], "lvn": [],
                    "lvn_ranges": [], "value_area": {}}

        price_bins = np.linspace(tail["low"].min(), tail["high"].max(), bins + 1)
        vol_profile = np.zeros(bins)

        for _, row in tail.iterrows():
            lo_bin = np.clip(int((row["low"] - price_bins[0]) / (price_range / bins)), 0, bins - 1)
            hi_bin = np.clip(int((row["high"] - price_bins[0]) / (price_range / bins)), 0, bins - 1)
            if lo_bin == hi_bin:
                vol_profile[lo_bin] += row["volume"]
            else:
                spread = hi_bin - lo_bin + 1
                for b in range(lo_bin, hi_bin + 1):
                    vol_profile[b] += row["volume"] / spread

        poc_idx = int(np.argmax(vol_profile))
        poc = float((price_bins[poc_idx] + price_bins[poc_idx + 1]) / 2)

        threshold_low = np.percentile(vol_profile[vol_profile > 0], 25) if vol_profile.sum() > 0 else 0
        lvn_prices = []
        lvn_ranges = []
        for i in range(bins):
            if 0 < vol_profile[i] <= threshold_low:
                lo_p = float(price_bins[i])
                hi_p = float(price_bins[i + 1])
                lvn_prices.append(round((lo_p + hi_p) / 2, 6))
                lvn_ranges.append({"low": round(lo_p, 6), "high": round(hi_p, 6)})

        threshold_high = np.percentile(vol_profile, 80)
        hvn = [round(float((price_bins[i] + price_bins[i + 1]) / 2), 6)
               for i in range(bins) if vol_profile[i] >= threshold_high]

        return {
            "poc": round(poc, 6),
            "hvn": hvn[:5],
            "lvn": lvn_prices[:5],
            "lvn_ranges": lvn_ranges[:5],
            "value_area": {},
        }

    @staticmethod
    def _is_in_lvn(price: float, lvn_ranges: list[dict], margin_pct: float = 0.3) -> dict | None:
        """현재가가 LVN 구간 안(또는 근처)에 있는지 확인"""
        for lr in lvn_ranges:
            lo = lr["low"]
            hi = lr["high"]
            rng = hi - lo
            expanded_lo = lo - rng * margin_pct
            expanded_hi = hi + rng * margin_pct
            if expanded_lo <= price <= expanded_hi:
                return lr
        return None

    @staticmethod
    def _sideways_score(df: pd.DataFrame, lookback: int = 20) -> float:
        """횡보 강도 (0=강한 추세, 1=완전 횡보)"""
        tail = df.tail(lookback)
        if len(tail) < 10:
            return 0.0

        close = tail["close"].values
        high = tail["high"].values
        low = tail["low"].values

        price_range = (high.max() - low.min()) / close.mean() * 100
        atr_vals = np.abs(np.diff(close)) / close[:-1] * 100
        avg_move = atr_vals.mean() if len(atr_vals) > 0 else 0

        net_change = abs(close[-1] - close[0]) / close[0] * 100

        if price_range < 0.01:
            return 1.0

        range_score = max(0, 1 - price_range / 5.0)
        move_score = max(0, 1 - avg_move / 1.0)
        net_score = max(0, 1 - net_change / 2.0)

        return round(range_score * 0.3 + move_score * 0.3 + net_score * 0.4, 3)

    @staticmethod
    def _relative_strength_vs_btc(alt_df: pd.DataFrame, btc_df: pd.DataFrame,
                                   lookback: int = 24) -> float:
        """BTC 대비 상대강도 (-1 ~ +1). 양수 = 알트가 BTC보다 강함"""
        alt_tail = alt_df.tail(lookback)
        btc_tail = btc_df.tail(lookback)
        if len(alt_tail) < 5 or len(btc_tail) < 5:
            return 0.0

        alt_ret = (float(alt_tail["close"].iloc[-1]) / float(alt_tail["close"].iloc[0]) - 1) * 100
        btc_ret = (float(btc_tail["close"].iloc[-1]) / float(btc_tail["close"].iloc[0]) - 1) * 100

        diff = alt_ret - btc_ret
        return round(max(-1, min(1, diff / 5.0)), 3)

    def collect_data(self) -> dict:
        """BTC + 워치리스트 전체 스캔"""
        data = {"btc": {}, "alts": {}}

        # BTC 4h 데이터
        try:
            btc_ohlcv = self.exchange.fetch_ohlcv("BTC/USDT", timeframe="4h", limit=60)
            btc_df = pd.DataFrame(btc_ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
            btc_close = btc_df["close"]
            btc_ret_24h = (float(btc_close.iloc[-1]) / float(btc_close.iloc[-6]) - 1) * 100

            ema12 = float(btc_close.ewm(span=12).mean().iloc[-1])
            ema26 = float(btc_close.ewm(span=26).mean().iloc[-1])

            delta = btc_close.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rsi = float((100 - (100 / (1 + gain / (loss + 1e-9)))).iloc[-1])

            btc_bearish = ema12 < ema26 and btc_ret_24h < -0.5

            btc_trend_str = "N/A"
            if self.btc_trend:
                self.btc_trend.update()
                btc_trend_str = self.btc_trend.trend_name

            data["btc"] = {
                "price": float(btc_close.iloc[-1]),
                "ret_24h": round(btc_ret_24h, 2),
                "ema12": round(ema12, 2),
                "ema26": round(ema26, 2),
                "rsi": round(rsi, 1),
                "bearish": btc_bearish,
                "trend_name": btc_trend_str,
            }
            data["_btc_df"] = btc_df
        except Exception as e:
            data["btc"] = {"error": str(e), "bearish": False}
            data["_btc_df"] = pd.DataFrame()

        # 알트코인 스캔
        btc_df = data.get("_btc_df", pd.DataFrame())
        candidates = []

        for sym in self.watchlist:
            if "BTC" in sym:
                continue
            try:
                ohlcv = self.exchange.fetch_ohlcv(sym, timeframe="4h", limit=60)
                df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
                if len(df) < 30:
                    continue

                vp = self._compute_volume_profile(df, lookback=50, bins=30)
                cur_price = float(df["close"].iloc[-1])

                lvn_hit = self._is_in_lvn(cur_price, vp.get("lvn_ranges", []))
                if not lvn_hit:
                    continue

                sw_score = self._sideways_score(df, lookback=20)
                if sw_score < 0.3:
                    continue

                rs = self._relative_strength_vs_btc(df, btc_df, lookback=24) if len(btc_df) > 0 else 0

                close = df["close"]
                ema12 = float(close.ewm(span=12).mean().iloc[-1])
                ema26 = float(close.ewm(span=26).mean().iloc[-1])
                ema_gap = abs(ema12 - ema26) / ema26 * 100

                delta = close.diff()
                gain = delta.where(delta > 0, 0).rolling(14).mean()
                loss_s = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rsi = float((100 - (100 / (1 + gain / (loss_s + 1e-9)))).iloc[-1])

                high = df["high"]
                low = df["low"]
                tr = pd.concat([high - low, (high - close.shift(1)).abs(),
                                (low - close.shift(1)).abs()], axis=1).max(axis=1)
                atr = float(tr.rolling(14).mean().iloc[-1])
                atr_pct = atr / cur_price * 100

                nearest_hvn_above = min([h for h in vp["hvn"] if h > cur_price], default=0)
                nearest_hvn_below = max([h for h in vp["hvn"] if h < cur_price], default=0)

                coin = sym.replace("/USDT", "")
                candidates.append({
                    "symbol": sym,
                    "coin": coin,
                    "price": cur_price,
                    "lvn_range": lvn_hit,
                    "sideways_score": sw_score,
                    "relative_strength": rs,
                    "ema_gap_pct": round(ema_gap, 3),
                    "rsi": round(rsi, 1),
                    "atr_pct": round(atr_pct, 3),
                    "poc": vp["poc"],
                    "hvn_above": nearest_hvn_above,
                    "hvn_below": nearest_hvn_below,
                    "volume_ratio": round(float(df["volume"].iloc[-1]) / max(float(df["volume"].tail(20).mean()), 1), 2),
                })

                import time
                time.sleep(0.08)
            except Exception:
                continue

        candidates.sort(key=lambda x: (x["relative_strength"], x["sideways_score"]), reverse=True)
        data["candidates"] = candidates[:10]

        return data

    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
        """후보 코인 LLM 분석"""
        btc = collected_data.get("btc", {})
        candidates = collected_data.get("candidates", [])

        if not candidates:
            return self._build_message(
                data={"candidates": [], "btc_status": btc.get("trend_name", "N/A"),
                      "scan_summary": "LVN 횡보 강세 알트 없음"},
                confidence=0.5, reasoning="조건 충족 코인 없음"
            )

        cand_text = ""
        for i, c in enumerate(candidates[:5]):
            cand_text += f"""
### {i+1}. {c['coin']} (${c['price']})
- LVN 범위: {c['lvn_range']['low']} ~ {c['lvn_range']['high']}
- 횡보 강도: {c['sideways_score']:.2f} (1=완전횡보)
- BTC 대비 상대강도: {c['relative_strength']:+.3f}
- EMA 갭: {c['ema_gap_pct']:.3f}% | RSI: {c['rsi']} | ATR: {c['atr_pct']:.3f}%
- 거래량 비율: {c['volume_ratio']}x
- 위 HVN: {c['hvn_above']} | 아래 HVN: {c['hvn_below']}
"""

        prompt = f"""BTC가 하락하는 중에도 LVN에서 횡보하며 버티는 알트코인 후보를 분석하세요.

## BTC 현재 상태
- 가격: ${btc.get('price', 'N/A')}
- 24h 수익률: {btc.get('ret_24h', 'N/A')}%
- 트렌드: {btc.get('trend_name', 'N/A')}
- RSI: {btc.get('rsi', 'N/A')}
- 하방 여부: {'예' if btc.get('bearish') else '아니오'}

## 후보 코인 ({len(candidates)}개 감지)
{cand_text}

각 후보의 돌파 가능성, 방향, 목표가를 분석하세요.
LVN 횡보 + BTC 대비 강세가 클수록 돌파 확률이 높습니다.
위 HVN이 있으면 상방 목표, 아래 HVN이 있으면 하방 리스크입니다."""

        result = self.llm_json(prompt, deep=True, max_tokens=3000)

        if result.get("parse_error"):
            result = self._fallback_result(collected_data)

        return self._build_message(
            data=result,
            confidence=result.get("candidates", [{}])[0].get("confidence", 0.5) if result.get("candidates") else 0.5,
            reasoning=result.get("scan_summary", ""),
        )

    def _fallback_result(self, data: dict) -> dict:
        """LLM 실패 시 규칙 기반 폴백"""
        btc = data.get("btc", {})
        candidates = data.get("candidates", [])

        result_cands = []
        for c in candidates[:5]:
            target = c["hvn_above"] if c["hvn_above"] else c["price"] * 1.03
            result_cands.append({
                "coin": c["coin"],
                "current_price": c["price"],
                "lvn_range": c["lvn_range"],
                "sideways_score": c["sideways_score"],
                "relative_strength": c["relative_strength"],
                "breakout_direction": "up" if c["relative_strength"] > 0 else "down",
                "breakout_target": round(target, 6),
                "confidence": round(min(c["sideways_score"] * 0.5 + max(c["relative_strength"], 0) * 0.5, 0.9), 2),
                "reason": f"LVN 횡보(강도 {c['sideways_score']:.2f}), BTC 대비 상대강도 {c['relative_strength']:+.3f}"
            })

        return {
            "candidates": result_cands,
            "btc_status": f"{btc.get('trend_name', 'N/A')} (24h: {btc.get('ret_24h', 0):+.1f}%)",
            "scan_summary": f"규칙 기반: {len(result_cands)}개 후보 (BTC {'하방' if btc.get('bearish') else '횡보/상방'})"
        }

    def format_telegram(self, msg: AgentMessage) -> str:
        """텔레그램 메시지 포맷팅"""
        d = msg.data
        candidates = d.get("candidates", [])
        btc_status = d.get("btc_status", "N/A")
        summary = d.get("scan_summary", "")

        lines = [
            "🔍 돌파 전문가 스캔 결과",
            f"{'━' * 24}",
            f"BTC: {btc_status}",
        ]

        if not candidates:
            lines.append("\n조건 충족 코인 없음")
        else:
            lines.append(f"\n후보: {len(candidates)}개")
            for i, c in enumerate(candidates[:5]):
                coin = c.get("coin", "?")
                price = c.get("current_price", 0)
                sw = c.get("sideways_score", 0)
                rs = c.get("relative_strength", 0)
                direction = c.get("breakout_direction", "?")
                target = c.get("breakout_target", 0)
                conf = c.get("confidence", 0)
                lvn = c.get("lvn_range", {})

                dir_emoji = "🟢" if direction == "up" else "🔴"
                rs_emoji = "💪" if rs > 0.1 else "➡️" if rs > -0.1 else "📉"

                lines.append(f"\n{dir_emoji} {i+1}. {coin} ${price}")
                lines.append(f"  LVN: {lvn.get('low', '?')} ~ {lvn.get('high', '?')}")
                lines.append(f"  횡보: {sw:.0%} | 상대강도: {rs_emoji}{rs:+.3f}")
                if target:
                    pnl = ((target - price) / price) * 20 * 100
                    lines.append(f"  목표: ${target} ({pnl:+.1f}% @20x)")
                lines.append(f"  신뢰도: {conf:.0%}")

                reason = c.get("reason", "")
                if reason:
                    lines.append(f"  → {reason[:60]}")

        if summary:
            lines.append(f"\n💡 {summary[:200]}")

        return "\n".join(lines)
