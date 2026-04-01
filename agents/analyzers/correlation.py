"""
correlation.py — Agent 3: 상관관계 분석가
==========================================
BTC와 타 자산 간 연동성 분석.
분석 유형: Factual (사실 기반)

데이터 소스:
- Binance (BTC, ETH, SOL, BNB + 추가 알트)
- Rolling 상관계수 (24h / 72h / 7d 윈도우)
- Regime 변화 감지 (상관 구조 전환점)
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from typing import Any

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage


# 상관 분석 대상 (BTC + 주요 알트 8개)
CORR_PAIRS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "AVAX", "LINK", "SUI"]


class CorrelationAgent(AgentBase):
    """
    상관관계 분석가

    책임:
    - BTC vs 알트 Rolling 상관계수 (다중 윈도우)
    - 상관 구조 Regime 변화 감지 (안정 → 분산 전환)
    - BTC/ETH 비율 추이 (BTC 도미넌스 프록시)
    - 알트 동조화 vs 분산 판단
    """

    def __init__(self, exchange=None):
        super().__init__(
            agent_id="agent_3_corr",
            agent_name="상관관계 분석가",
            role_description="BTC와 타 자산 간 연동성 분석 (Factual)"
        )
        self._analysis_type = "factual"
        if exchange is None:
            import ccxt
            exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        self.exchange = exchange

    def _get_system_prompt(self) -> str:
        return """당신은 자산 간 상관관계 전문 분석가입니다.
크립토 자산들 간의 연동성을 분석하여 시장 안정성을 판단합니다.

분석 원칙:
- 높은 상관관계 → 시장 동조화 (방향성 명확)
- 낮은 상관관계 → 분산 (불확실성 증가)
- 상관관계 급변(breakdown) → 레짐 전환 신호
- 20배 레버리지에서 불확실성 = 위험

반드시 JSON 형식으로 출력:
```json
{
  "btc_eth_correlation": {"24h": 상관계수, "72h": 상관계수, "7d": 상관계수},
  "btc_eth_ratio_trend": "rising|stable|falling",
  "alt_synchronization": 0.0~1.0(동조도),
  "correlation_regime": "high_sync|normal|decorrelating|breakdown",
  "regime_transition": "상관 구조 변화 방향 설명",
  "pair_correlations": {"ETH": 0.xx, "SOL": 0.xx, ...},
  "risk_level": "SAFE|CAUTION|DANGER",
  "confidence": 0.0~1.0,
  "reasoning": "분석 근거"
}
```"""

    def collect_data(self) -> dict:
        """상관관계 데이터 수집 (7일 1시간봉)"""
        data = {}
        import time as _time

        for coin in CORR_PAIRS:
            try:
                ohlcv = self.exchange.fetch_ohlcv(f"{coin}/USDT", "1h", limit=168)  # 7일
                df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
                data[coin] = {
                    "prices": df["close"].values.tolist(),
                    "volumes": df["volume"].values.tolist(),
                }
                _time.sleep(0.05)
            except Exception:
                pass

        return data

    def analyze(self, collected_data: dict, context: dict | None = None) -> AgentMessage:
        """상관관계 분석 수행"""
        btc_data = collected_data.get("BTC", {})
        btc = btc_data.get("prices", []) if isinstance(btc_data, dict) else btc_data

        # 알트 가격 추출
        alts = {}
        for coin in CORR_PAIRS:
            if coin == "BTC":
                continue
            cd = collected_data.get(coin, {})
            prices = cd.get("prices", []) if isinstance(cd, dict) else cd
            if prices:
                alts[coin] = prices

        # 핵심 계산
        result = self._compute_rolling_correlations(btc, alts)
        regime = self._detect_regime_change(btc, alts)
        result.update(regime)

        # LLM 보강 분석
        if btc and alts:
            try:
                pair_corrs = result.get("pair_correlations", {})
                corr_windows = result.get("btc_eth_correlation", {})
                prompt = f"""다음 상관관계 데이터를 분석하세요.

## BTC-ETH 상관계수 (다중 윈도우)
- 24h: {corr_windows.get('24h', 'N/A'):.3f}
- 72h: {corr_windows.get('72h', 'N/A'):.3f}
- 7d:  {corr_windows.get('7d', 'N/A'):.3f}

## BTC-알트 상관계수 (7일)
{chr(10).join(f'- BTC-{k}: {v:.3f}' for k, v in pair_corrs.items())}

## BTC/ETH 비율
- 현재: {result.get('btc_eth_ratio', 'N/A'):.2f}
- 7일 전: {result.get('btc_eth_ratio_7d_ago', 'N/A'):.2f}
- 트렌드: {result.get('btc_eth_ratio_trend', 'N/A')}

## Regime 분석
- 현재 상태: {result.get('correlation_regime', 'N/A')}
- 전환 감지: {result.get('regime_transition', 'N/A')}
- 평균 동조도: {result.get('alt_synchronization', 'N/A'):.3f}
- Rolling 상관 변화: {result.get('corr_trend_desc', 'N/A')}

20배 레버리지 환경에서 상관관계 변화가 의미하는 바를 분석하세요."""

                llm_result = self.llm_json(prompt, deep=True)
                if not llm_result.get("parse_error"):
                    # LLM 결과를 병합하되 계산된 데이터는 유지
                    for key in ("risk_level", "confidence", "reasoning",
                                "correlation_regime", "regime_transition"):
                        if key in llm_result:
                            result[key] = llm_result[key]
            except Exception:
                pass

        warnings = []
        if result.get("correlation_regime") in ("decorrelating", "breakdown"):
            warnings.append(f"⚠️ 상관 {result['correlation_regime']} — 레짐 전환 주의")
        if result.get("alt_synchronization", 1) < 0.3:
            warnings.append(f"📊 알트 동조도 {result['alt_synchronization']:.2f} — 방향성 불명확")
        if result.get("btc_eth_ratio_trend") == "rising":
            warnings.append("₿ BTC 도미넌스 상승 — 알트 약세 가능")

        return self._build_message(
            data=result,
            confidence=result.get("confidence", 0.5),
            reasoning=result.get("reasoning", ""),
            warnings=warnings
        )

    # ── Rolling 상관계수 (다중 윈도우) ──

    def _compute_rolling_correlations(self, btc: list, alts: dict[str, list]) -> dict:
        """다중 윈도우 Rolling 상관계수 계산"""
        result = {
            "btc_eth_correlation": {"24h": 0.0, "72h": 0.0, "7d": 0.0},
            "btc_eth_ratio": 0.0,
            "btc_eth_ratio_7d_ago": 0.0,
            "btc_eth_ratio_trend": "stable",
            "pair_correlations": {},
            "alt_synchronization": 0.5,
            "risk_level": "CAUTION",
            "confidence": 0.4,
            "reasoning": "데이터 기반 상관관계 계산",
        }

        if not btc or len(btc) < 48:
            result["reasoning"] = "BTC 데이터 불충분"
            return result

        btc_arr = np.array(btc)
        btc_ret = np.diff(btc_arr) / btc_arr[:-1]

        # 각 알트와 BTC 상관계수 (7일, 72h, 24h)
        windows = {"24h": 24, "72h": 72, "7d": len(btc_ret)}
        all_corrs_7d = []

        for coin, prices in alts.items():
            min_len = min(len(btc), len(prices))
            if min_len < 48:
                continue

            alt_arr = np.array(prices[-min_len:])
            alt_ret = np.diff(alt_arr) / alt_arr[:-1]
            btc_ret_aligned = btc_ret[-len(alt_ret):]

            # 다중 윈도우 상관
            coin_corrs = {}
            for wname, wsize in windows.items():
                n = min(wsize, len(btc_ret_aligned))
                if n >= 12:
                    c = float(np.corrcoef(btc_ret_aligned[-n:], alt_ret[-n:])[0, 1])
                    coin_corrs[wname] = round(c, 3)
                else:
                    coin_corrs[wname] = 0.0

            result["pair_correlations"][coin] = coin_corrs.get("7d", 0.0)

            if coin == "ETH":
                result["btc_eth_correlation"] = coin_corrs
                # BTC/ETH 비율
                if alt_arr[-1] > 0 and alt_arr[0] > 0:
                    result["btc_eth_ratio"] = round(float(btc_arr[-1] / alt_arr[-1]), 2)
                    result["btc_eth_ratio_7d_ago"] = round(float(btc_arr[-min_len] / alt_arr[0]), 2)
                    ratio_change = result["btc_eth_ratio"] - result["btc_eth_ratio_7d_ago"]
                    if ratio_change > 0.5:
                        result["btc_eth_ratio_trend"] = "rising"
                    elif ratio_change < -0.5:
                        result["btc_eth_ratio_trend"] = "falling"

            all_corrs_7d.append(coin_corrs.get("7d", 0.0))

        # 동조도 = 평균 7일 상관계수 (0~1 정규화)
        if all_corrs_7d:
            avg_corr = float(np.mean(all_corrs_7d))
            result["alt_synchronization"] = round(max(avg_corr, 0), 3)

        # 위험도
        sync = result["alt_synchronization"]
        if sync < 0.2:
            result["risk_level"] = "DANGER"
        elif sync < 0.4:
            result["risk_level"] = "CAUTION"
        else:
            result["risk_level"] = "SAFE"

        result["confidence"] = 0.6
        return result

    # ── Regime 변화 감지 ──

    def _detect_regime_change(self, btc: list, alts: dict[str, list]) -> dict:
        """
        상관 구조의 Regime 전환 감지.
        방법: 24h 윈도우 Rolling 상관의 표준편차 + 급변 감지.
        """
        result = {
            "correlation_regime": "normal",
            "regime_transition": "안정",
            "corr_trend_desc": "상관 구조 변화 없음",
        }

        if not btc or len(btc) < 72:
            return result

        btc_arr = np.array(btc)
        btc_ret = np.diff(btc_arr) / btc_arr[:-1]

        # ETH와의 Rolling 상관 (24h 윈도우, 1시간 슬라이딩)
        eth_prices = alts.get("ETH", [])
        if not eth_prices or len(eth_prices) < 72:
            return result

        min_len = min(len(btc), len(eth_prices))
        eth_arr = np.array(eth_prices[-min_len:])
        eth_ret = np.diff(eth_arr) / eth_arr[:-1]
        btc_ret_a = btc_ret[-len(eth_ret):]

        window = 24
        rolling_corrs = []
        for i in range(window, len(btc_ret_a)):
            c = float(np.corrcoef(btc_ret_a[i-window:i], eth_ret[i-window:i])[0, 1])
            rolling_corrs.append(c)

        if len(rolling_corrs) < 10:
            return result

        rc = np.array(rolling_corrs)

        # 최근 24h vs 이전 구간 비교
        recent = rc[-24:] if len(rc) >= 24 else rc[-len(rc)//2:]
        earlier = rc[:-24] if len(rc) >= 48 else rc[:len(rc)//2]

        avg_recent = float(np.mean(recent))
        avg_earlier = float(np.mean(earlier))
        std_all = float(np.std(rc))
        diff = avg_recent - avg_earlier

        # Regime 분류
        if avg_recent >= 0.7:
            regime = "high_sync"
            desc = f"높은 동조 ({avg_recent:.2f})"
        elif avg_recent >= 0.4:
            regime = "normal"
            desc = f"정상 범위 ({avg_recent:.2f})"
        elif avg_recent >= 0.1:
            regime = "decorrelating"
            desc = f"상관 약화 ({avg_recent:.2f})"
        else:
            regime = "breakdown"
            desc = f"상관 붕괴 ({avg_recent:.2f})"

        # 전환 방향 감지
        if diff < -0.2:
            transition = f"동조→분산 전환 (diff: {diff:+.2f})"
        elif diff > 0.2:
            transition = f"분산→동조 회복 (diff: {diff:+.2f})"
        elif std_all > 0.3:
            transition = f"불안정 (상관 변동성: {std_all:.2f})"
            if regime == "normal":
                regime = "decorrelating"
        else:
            transition = "안정"

        result["correlation_regime"] = regime
        result["regime_transition"] = transition
        result["corr_trend_desc"] = (f"{desc}, 최근 {avg_recent:.2f} vs 이전 {avg_earlier:.2f}, "
                                      f"변동성 {std_all:.2f}")

        return result
