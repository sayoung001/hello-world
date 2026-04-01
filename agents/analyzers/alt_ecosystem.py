"""
alt_ecosystem.py — Agent 4: 알트코인 생태계 분석가
===================================================
알트코인 시장 전체 동향, 분산도, 섹터 모멘텀 분석.
분석 유형: Subjective (주관적 해석)

데이터 소스:
- Binance (상위 알트코인 가격)
- 분산도 지수(Dispersion Index) 계산
- 섹터별 퍼포먼스 분류
"""

from __future__ import annotations
import pandas as pd
import numpy as np
import time
from typing import Any

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage


# 섹터 분류 (v9 워치리스트 기반 + 확장)
SECTOR_MAP = {
    # L1
    "SUI": "L1", "SOL": "L1", "AVAX": "L1", "APT": "L1", "SEI": "L1", "TIA": "L1",
    # AI
    "WLD": "AI", "FET": "AI", "RENDER": "AI", "TAO": "AI",
    # 밈
    "DOGE": "Meme", "PEPE": "Meme", "WIF": "Meme", "BONK": "Meme", "FLOKI": "Meme",
    # DeFi
    "UNI": "DeFi", "AAVE": "DeFi", "MKR": "DeFi", "CRV": "DeFi",
    # 인프라
    "LINK": "Infra", "DOT": "Infra", "ATOM": "Infra", "ARB": "Infra", "OP": "Infra",
    # 기타
    "XRP": "Payment", "BNB": "Exchange", "LTC": "Legacy",
}

# 분석 대상 (시총 상위 + v9 워치리스트)
ANALYSIS_COINS = [
    "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "DOT",
    "LINK", "SUI", "APT", "WLD", "SEI", "PEPE", "WIF", "ARB",
    "OP", "FET", "RENDER", "UNI",
]


class AltEcosystemAgent(AgentBase):
    """
    알트코인 생태계 분석가

    책임:
    - Dispersion Index 계산 (알트 간 방향 일치도)
    - BTC 상승 시 알트 베타 (과민반응 정도)
    - 섹터별 강세/약세 판단
    - 현재 포지션 코인의 생태계 맥락 분석
    """

    def __init__(self, exchange=None):
        super().__init__(
            agent_id="agent_4_alt",
            agent_name="알트 생태계 분석가",
            role_description="알트코인 시장 동향 및 분산도 분석 (Subjective)"
        )
        self._analysis_type = "subjective"
        if exchange is None:
            import ccxt
            exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        self.exchange = exchange

    def _get_system_prompt(self) -> str:
        return """당신은 알트코인 생태계 전문 분석가입니다.
BTC 도미넌스, 섹터 로테이션, 알트코인 분산도를 분석합니다.

분석 원칙:
- 분산도가 높으면 시장 불안정 (포지션 사이즈 보수적)
- BTC 도미넌스 상승 = 알트에 불리
- 섹터 모멘텀이 포지션 방향과 일치하는지 확인
- 20배 레버리지에서는 강한 섹터 모멘텀만 신뢰

반드시 JSON 형식으로 출력:
```json
{
  "dispersion_index": 0~1(0=완전동조, 1=완전분산),
  "market_consensus": "bullish|bearish|unclear",
  "btc_dominance_trend": "rising|stable|falling",
  "leading_sector": "섹터명",
  "lagging_sector": "섹터명",
  "sector_performance": {"L1": 변동률%, "AI": %, "Meme": %, ...},
  "alt_beta": BTC 대비 알트 베타 계수,
  "risk_level": "SAFE|CAUTION|DANGER",
  "confidence": 0.0~1.0,
  "reasoning": "분석 근거"
}
```"""

    def collect_data(self) -> dict:
        """알트코인 데이터 수집"""
        data = {"coins": {}, "btc": []}

        # BTC 기준 데이터
        try:
            ohlcv = self.exchange.fetch_ohlcv("BTC/USDT", "1h", limit=24)
            df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
            data["btc"] = df["close"].values.tolist()
        except Exception:
            pass

        # 알트코인 데이터
        for coin in ANALYSIS_COINS:
            try:
                ohlcv = self.exchange.fetch_ohlcv(f"{coin}/USDT", "1h", limit=24)
                df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
                data["coins"][coin] = {
                    "prices": df["close"].values.tolist(),
                    "volume": float(df["volume"].tail(4).mean()),
                    "change_4h": round(
                        (df["close"].iloc[-1] - df["close"].iloc[-4]) / df["close"].iloc[-4] * 100, 2
                    ) if len(df) >= 5 else 0,
                    "change_24h": round(
                        (df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100, 2
                    ) if len(df) >= 2 else 0,
                    "sector": SECTOR_MAP.get(coin, "Other"),
                }
                time.sleep(0.05)
            except Exception:
                pass

        return data

    def analyze(self, collected_data: dict, context: dict | None = None) -> AgentMessage:
        """알트코인 생태계 분석"""
        coins = collected_data.get("coins", {})
        btc_prices = collected_data.get("btc", [])

        result = self._compute_ecosystem_metrics(coins, btc_prices)

        # LLM 보강 분석
        if coins:
            try:
                sector_perf = result.get("sector_performance", {})
                top_movers = sorted(
                    [(c, d.get("change_4h", 0)) for c, d in coins.items()],
                    key=lambda x: abs(x[1]), reverse=True
                )[:5]

                prompt = f"""다음 알트코인 생태계 데이터를 분석하세요.

## 분산도
- Dispersion Index: {result.get('dispersion_index', 'N/A'):.3f}

## 섹터별 4시간 수익률
{chr(10).join(f'- {k}: {v:+.2f}%' for k, v in sector_perf.items())}

## 상위 변동 코인 (4h)
{chr(10).join(f'- {c} ({SECTOR_MAP.get(c, "?")}): {ch:+.2f}%' for c, ch in top_movers)}

## BTC 대비 알트 베타
- 평균 베타: {result.get('alt_beta', 'N/A'):.2f}

20배 레버리지 환경에서 알트코인 포지션 관리에 대한 판단을 내려주세요."""

                llm_result = self.llm_json(prompt, deep=True)
                if not llm_result.get("parse_error"):
                    result.update(llm_result)
            except Exception:
                pass

        warnings = []
        if result.get("dispersion_index", 0) > 0.7:
            warnings.append("⚠️ 알트 분산도 높음 — 시장 불안정")
        if result.get("alt_beta", 1.0) > 2.0:
            warnings.append(f"⚡ 알트 베타 {result['alt_beta']:.1f}x — 과민반응 구간")

        return self._build_message(
            data=result,
            confidence=result.get("confidence", 0.5),
            reasoning=result.get("reasoning", ""),
            warnings=warnings
        )

    def _compute_ecosystem_metrics(self, coins: dict, btc_prices: list) -> dict:
        """생태계 지표 계산"""
        result = {
            "dispersion_index": 0.5,
            "market_consensus": "unclear",
            "btc_dominance_trend": "stable",
            "leading_sector": "N/A",
            "lagging_sector": "N/A",
            "sector_performance": {},
            "alt_beta": 1.0,
            "risk_level": "CAUTION",
            "confidence": 0.4,
            "reasoning": "데이터 기반 생태계 분석",
        }

        if not coins:
            return result

        # 1. 분산도 (Dispersion Index)
        changes = [d.get("change_4h", 0) for d in coins.values() if d.get("change_4h") is not None]
        if changes:
            positive = sum(1 for c in changes if c > 0)
            negative = sum(1 for c in changes if c < 0)
            total = len(changes)
            # 완전 동조(모두 같은 방향) = 0, 완전 분산(반반) = 1
            dispersion = 1.0 - abs(positive - negative) / max(total, 1)
            result["dispersion_index"] = round(dispersion, 3)

            # 시장 컨센서스
            if positive > total * 0.7:
                result["market_consensus"] = "bullish"
            elif negative > total * 0.7:
                result["market_consensus"] = "bearish"
            else:
                result["market_consensus"] = "unclear"

        # 2. 섹터별 퍼포먼스
        sector_returns: dict[str, list[float]] = {}
        for coin, info in coins.items():
            sector = info.get("sector", "Other")
            change = info.get("change_4h", 0)
            sector_returns.setdefault(sector, []).append(change)

        sector_avg = {s: round(np.mean(returns), 2) for s, returns in sector_returns.items() if returns}
        result["sector_performance"] = sector_avg

        if sector_avg:
            result["leading_sector"] = max(sector_avg, key=sector_avg.get)
            result["lagging_sector"] = min(sector_avg, key=sector_avg.get)

        # 3. 알트 베타 (BTC 대비 변동성)
        if btc_prices and len(btc_prices) >= 5:
            btc_ret = np.diff(btc_prices) / np.array(btc_prices[:-1])
            btc_change = (btc_prices[-1] - btc_prices[-4]) / btc_prices[-4] * 100 if len(btc_prices) > 4 else 0

            if abs(btc_change) > 0.1:
                alt_changes = [d.get("change_4h", 0) for d in coins.values()]
                avg_alt_change = np.mean(alt_changes) if alt_changes else 0
                result["alt_beta"] = round(avg_alt_change / btc_change, 2) if btc_change != 0 else 1.0

        # 위험도
        if result["dispersion_index"] > 0.7:
            result["risk_level"] = "CAUTION"
        elif result["market_consensus"] == "unclear":
            result["risk_level"] = "CAUTION"
        else:
            result["risk_level"] = "SAFE"

        result["confidence"] = 0.5
        return result
