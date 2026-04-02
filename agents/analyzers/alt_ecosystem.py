"""
alt_ecosystem.py — Agent 4: 알트코인 생태계 분석가
===================================================
알트코인 시장 전체 동향, 분산도, 섹터 모멘텀 분석.
분석 유형: Subjective (주관적 해석)

데이터 소스:
- Binance (상위 알트코인 가격 + 거래량)
- Dispersion Index (방향 + 크기 기반)
- 섹터별 Rolling 모멘텀
- 포지션별 BTC 베타 계산
"""

from __future__ import annotations
import pandas as pd
import numpy as np
import time as _time
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
    "XRP": "Payment", "BNB": "Exchange", "LTC": "Legacy", "ETH": "L1",
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
    - Dispersion Index 계산 (방향 + 크기 기반 고도화)
    - 섹터별 Rolling 모멘텀 (4h / 24h / 7d)
    - 포지션별 BTC 베타 계산 (개별 코인 리스크)
    - 섹터 로테이션 감지
    - Altcoin Season Index (BTC 대비 알트 outperformance 비율)
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
  "dispersion_detail": {"direction": 방향분산, "magnitude": 크기분산},
  "market_consensus": "bullish|bearish|unclear",
  "btc_dominance_trend": "rising|stable|falling",
  "leading_sector": "섹터명",
  "lagging_sector": "섹터명",
  "sector_momentum": {"L1": {"4h": %, "24h": %, "7d": %}, ...},
  "sector_rotation": "섹터 로테이션 설명",
  "alt_beta": BTC 대비 알트 평균 베타,
  "coin_betas": {"SOL": 1.5, "DOGE": 2.1, ...},
  "alt_season_7d": 0~100 (7일 기준 BTC 대비 outperform %),
  "alt_season_24h": 0~100 (24h 기준),
  "alt_season_label": "btc_season|neutral|alt_leaning|alt_season",
  "risk_level": "SAFE|CAUTION|DANGER",
  "confidence": 0.0~1.0,
  "reasoning": "분석 근거"
}
```"""

    def collect_data(self) -> dict:
        """알트코인 데이터 수집 (7일 1시간봉)"""
        data = {"coins": {}, "btc": {}}

        # BTC 기준 데이터 (7일)
        try:
            ohlcv = self.exchange.fetch_ohlcv("BTC/USDT", "1h", limit=168)
            df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
            data["btc"] = {
                "prices": df["close"].values.tolist(),
                "volumes": df["volume"].values.tolist(),
            }
        except Exception:
            pass

        # 알트코인 데이터
        for coin in ANALYSIS_COINS:
            try:
                ohlcv = self.exchange.fetch_ohlcv(f"{coin}/USDT", "1h", limit=168)
                df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
                prices = df["close"].values.tolist()
                volumes = df["volume"].values.tolist()

                # 멀티타임프레임 변동률
                change_4h = (prices[-1] / prices[-4] - 1) * 100 if len(prices) >= 5 else 0
                change_24h = (prices[-1] / prices[-24] - 1) * 100 if len(prices) >= 25 else 0
                change_7d = (prices[-1] / prices[0] - 1) * 100 if len(prices) >= 2 else 0

                data["coins"][coin] = {
                    "prices": prices,
                    "volumes": volumes,
                    "change_4h": round(change_4h, 2),
                    "change_24h": round(change_24h, 2),
                    "change_7d": round(change_7d, 2),
                    "volume_24h": float(np.sum(volumes[-24:])) if len(volumes) >= 24 else 0,
                    "sector": SECTOR_MAP.get(coin, "Other"),
                }
                _time.sleep(0.05)
            except Exception:
                pass

        return data

    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
        """알트코인 생태계 분석"""
        coins = collected_data.get("coins", {})
        btc_data = collected_data.get("btc", {})
        btc_prices = btc_data.get("prices", []) if isinstance(btc_data, dict) else btc_data

        # 핵심 계산
        dispersion = self._compute_dispersion(coins)
        sector_mom = self._compute_sector_momentum(coins)
        betas = self._compute_coin_betas(coins, btc_prices)
        dominance = self._estimate_dominance_trend(coins, btc_prices)
        alt_season = self._compute_altcoin_season_index(coins, btc_prices)

        result = {**dispersion, **sector_mom, **betas, **dominance, **alt_season}

        # LLM 보강 분석
        if coins:
            try:
                sp = result.get("sector_momentum", {})
                top_movers = sorted(
                    [(c, d.get("change_4h", 0)) for c, d in coins.items()],
                    key=lambda x: abs(x[1]), reverse=True
                )[:5]

                prompt = f"""다음 알트코인 생태계 데이터를 분석하세요.

## 분산도
- Dispersion Index: {result.get('dispersion_index', 'N/A'):.3f}
  - 방향 분산: {result.get('dispersion_detail', {}).get('direction', 'N/A'):.3f}
  - 크기 분산: {result.get('dispersion_detail', {}).get('magnitude', 'N/A'):.3f}
- 시장 컨센서스: {result.get('market_consensus', 'N/A')}

## 섹터별 모멘텀
{chr(10).join(f'- {s}: 4h {m.get("4h", 0):+.2f}% / 24h {m.get("24h", 0):+.2f}% / 7d {m.get("7d", 0):+.2f}%' for s, m in sp.items())}

## 상위 변동 코인 (4h)
{chr(10).join(f'- {c} ({SECTOR_MAP.get(c, "?")}): {ch:+.2f}%' for c, ch in top_movers)}

## BTC 베타 (상위)
{chr(10).join(f'- {c}: {b:.2f}x' for c, b in sorted(result.get('coin_betas', {}).items(), key=lambda x: abs(x[1]), reverse=True)[:5])}

## BTC 도미넌스
- 추세: {result.get('btc_dominance_trend', 'N/A')}
- 알트 평균 베타: {result.get('alt_beta', 'N/A'):.2f}

## Altcoin Season Index
- 7일: {result.get('alt_season_7d', 0):.0f}% (BTC 대비 outperform 비율)
- 24시간: {result.get('alt_season_24h', 0):.0f}%
- 판정: {result.get('alt_season_label', 'N/A')}

## 섹터 로테이션
- 선두: {result.get('leading_sector', 'N/A')}
- 후미: {result.get('lagging_sector', 'N/A')}

20배 레버리지 환경에서 알트코인 포지션 관리에 대한 판단을 내려주세요."""

                llm_result = self.llm_json(prompt, deep=True)
                if not llm_result.get("parse_error"):
                    for key in ("risk_level", "confidence", "reasoning",
                                "sector_rotation", "recommendation"):
                        if key in llm_result:
                            result[key] = llm_result[key]
            except Exception:
                pass

        warnings = []
        if result.get("dispersion_index", 0) > 0.7:
            warnings.append("⚠️ 알트 분산도 높음 — 시장 방향성 불명확")
        if result.get("alt_beta", 1.0) > 2.0:
            warnings.append(f"⚡ 알트 베타 {result['alt_beta']:.1f}x — 과민반응 구간")
        if result.get("btc_dominance_trend") == "rising":
            warnings.append("₿ BTC 도미넌스 상승 — 알트 약세 주의")

        # Altcoin Season 경고
        alt_season_label = result.get("alt_season_label", "")
        if alt_season_label == "btc_season":
            warnings.append("₿ BTC 시즌 — 알트 롱 진입 주의")
        elif alt_season_label == "alt_season":
            warnings.append("🚀 알트 시즌 — 알트 모멘텀 강세")

        # 섹터 급락 경고
        for sector, mom in result.get("sector_momentum", {}).items():
            if mom.get("4h", 0) < -3:
                warnings.append(f"📉 {sector} 섹터 4h {mom['4h']:+.1f}% 급락")

        return self._build_message(
            data=result,
            confidence=result.get("confidence", 0.5),
            reasoning=result.get("reasoning", ""),
            warnings=warnings
        )

    # ── Dispersion Index (고도화) ──

    @staticmethod
    def _compute_dispersion(coins: dict) -> dict:
        """
        고도화된 Dispersion Index.
        기존: 방향만 (상승/하락 비율)
        개선: 방향 분산 + 크기 분산 결합
        """
        result = {
            "dispersion_index": 0.5,
            "dispersion_detail": {"direction": 0.5, "magnitude": 0.5},
            "market_consensus": "unclear",
        }

        changes_4h = [d.get("change_4h", 0) for d in coins.values() if d.get("change_4h") is not None]
        if not changes_4h:
            return result

        # 1. 방향 분산 (기존 개선): 상승/하락 비율
        positive = sum(1 for c in changes_4h if c > 0.1)
        negative = sum(1 for c in changes_4h if c < -0.1)
        neutral = len(changes_4h) - positive - negative
        total = len(changes_4h)
        direction_disp = 1.0 - abs(positive - negative) / max(total, 1)

        # 2. 크기 분산: 변동률의 분산계수(CV)
        abs_changes = [abs(c) for c in changes_4h]
        mean_change = np.mean(abs_changes)
        std_change = np.std(abs_changes)
        cv = std_change / max(mean_change, 0.01)
        magnitude_disp = min(cv / 2.0, 1.0)  # 0~1 정규화

        # 3. 종합: 방향 60% + 크기 40% (방향이 더 중요)
        combined = direction_disp * 0.6 + magnitude_disp * 0.4

        result["dispersion_index"] = round(combined, 3)
        result["dispersion_detail"] = {
            "direction": round(direction_disp, 3),
            "magnitude": round(magnitude_disp, 3),
        }

        # 시장 컨센서스
        if positive > total * 0.7:
            result["market_consensus"] = "bullish"
        elif negative > total * 0.7:
            result["market_consensus"] = "bearish"
        else:
            result["market_consensus"] = "unclear"

        return result

    # ── 섹터 모멘텀 (멀티타임프레임) ──

    @staticmethod
    def _compute_sector_momentum(coins: dict) -> dict:
        """섹터별 4h / 24h / 7d 모멘텀"""
        sector_data: dict[str, dict[str, list]] = {}

        for coin, info in coins.items():
            sector = info.get("sector", "Other")
            if sector not in sector_data:
                sector_data[sector] = {"4h": [], "24h": [], "7d": []}
            sector_data[sector]["4h"].append(info.get("change_4h", 0))
            sector_data[sector]["24h"].append(info.get("change_24h", 0))
            sector_data[sector]["7d"].append(info.get("change_7d", 0))

        sector_momentum = {}
        for sector, tf_data in sector_data.items():
            sector_momentum[sector] = {
                tf: round(float(np.mean(changes)), 2) if changes else 0
                for tf, changes in tf_data.items()
            }

        # 선두/후미 섹터 (24h 기준)
        leading = max(sector_momentum, key=lambda s: sector_momentum[s].get("24h", 0),
                      default="N/A") if sector_momentum else "N/A"
        lagging = min(sector_momentum, key=lambda s: sector_momentum[s].get("24h", 0),
                      default="N/A") if sector_momentum else "N/A"

        return {
            "sector_momentum": sector_momentum,
            "leading_sector": leading,
            "lagging_sector": lagging,
        }

    # ── 포지션별 BTC 베타 계산 ──

    @staticmethod
    def _compute_coin_betas(coins: dict, btc_prices: list) -> dict:
        """
        각 코인의 BTC 대비 베타 계수.
        베타 = Cov(coin, btc) / Var(btc) (72h 기준)
        """
        result = {"alt_beta": 1.0, "coin_betas": {}}

        if not btc_prices or len(btc_prices) < 72:
            return result

        btc_arr = np.array(btc_prices[-72:])
        btc_ret = np.diff(btc_arr) / btc_arr[:-1]
        btc_var = np.var(btc_ret)
        if btc_var < 1e-10:
            return result

        betas = {}
        for coin, info in coins.items():
            prices = info.get("prices", [])
            if len(prices) < 72:
                continue
            alt_arr = np.array(prices[-72:])
            alt_ret = np.diff(alt_arr) / alt_arr[:-1]

            min_len = min(len(btc_ret), len(alt_ret))
            cov = np.cov(btc_ret[-min_len:], alt_ret[-min_len:])[0, 1]
            beta = float(cov / btc_var)
            betas[coin] = round(beta, 2)

        result["coin_betas"] = betas
        if betas:
            result["alt_beta"] = round(float(np.mean(list(betas.values()))), 2)

        # 위험도
        result["risk_level"] = "CAUTION" if result["alt_beta"] > 1.5 else "SAFE"
        result["confidence"] = 0.5

        return result

    # ── Altcoin Season Index ──

    @staticmethod
    def _compute_altcoin_season_index(coins: dict, btc_prices: list) -> dict:
        """
        Altcoin Season Index: BTC 대비 outperform 하는 알트 비율.

        기준:
        - 75% 이상 → alt_season (알트 시즌)
        - 50~75% → alt_leaning (알트 우세)
        - 25~50% → neutral (중립)
        - 25% 미만 → btc_season (BTC 시즌)

        CMC Altcoin Season Index와 동일한 컨셉이나,
        실시간 Binance 데이터로 24h/7d 두 구간 측정.
        """
        result = {
            "alt_season_7d": 50.0,
            "alt_season_24h": 50.0,
            "alt_season_label": "neutral",
        }

        if not btc_prices or len(btc_prices) < 24:
            return result

        btc_change_24h = (btc_prices[-1] / btc_prices[-24] - 1) * 100
        btc_change_7d = (btc_prices[-1] / btc_prices[0] - 1) * 100 if len(btc_prices) >= 168 else None

        # 24h outperformance 비율
        outperform_24h = 0
        total_24h = 0
        for info in coins.values():
            ch = info.get("change_24h")
            if ch is not None:
                total_24h += 1
                if ch > btc_change_24h:
                    outperform_24h += 1

        if total_24h > 0:
            result["alt_season_24h"] = round(outperform_24h / total_24h * 100, 1)

        # 7d outperformance 비율
        if btc_change_7d is not None:
            outperform_7d = 0
            total_7d = 0
            for info in coins.values():
                ch = info.get("change_7d")
                if ch is not None:
                    total_7d += 1
                    if ch > btc_change_7d:
                        outperform_7d += 1
            if total_7d > 0:
                result["alt_season_7d"] = round(outperform_7d / total_7d * 100, 1)

        # 판정 (7d 기준, 24h 보조)
        idx_7d = result["alt_season_7d"]
        if idx_7d >= 75:
            result["alt_season_label"] = "alt_season"
        elif idx_7d >= 50:
            result["alt_season_label"] = "alt_leaning"
        elif idx_7d >= 25:
            result["alt_season_label"] = "neutral"
        else:
            result["alt_season_label"] = "btc_season"

        return result

    # ── BTC 도미넌스 추정 ──

    @staticmethod
    def _estimate_dominance_trend(coins: dict, btc_prices: list) -> dict:
        """
        BTC 도미넌스 추정: BTC가 알트보다 강하면 도미넌스 상승.
        """
        if not btc_prices or len(btc_prices) < 24:
            return {"btc_dominance_trend": "stable"}

        btc_change_24h = (btc_prices[-1] / btc_prices[-24] - 1) * 100

        alt_changes = [d.get("change_24h", 0) for d in coins.values()]
        avg_alt_change = float(np.mean(alt_changes)) if alt_changes else 0

        diff = btc_change_24h - avg_alt_change
        if diff > 1.0:
            trend = "rising"  # BTC가 알트보다 강함
        elif diff < -1.0:
            trend = "falling"  # 알트가 BTC보다 강함
        else:
            trend = "stable"

        return {
            "btc_dominance_trend": trend,
            "reasoning": (f"데이터 기반: BTC 24h {btc_change_24h:+.2f}%, "
                          f"알트 평균 {avg_alt_change:+.2f}%, 차이 {diff:+.2f}%"),
        }
