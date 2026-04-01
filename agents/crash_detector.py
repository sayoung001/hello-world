"""
crash_detector.py — 경량 시장 급락 감지 트리거
================================================
v9 메인 루프(15초)에 편승하여 BTC 가격만 빠르게 체크.
급락 감지 시 AgentHook이 전체 6 에이전트 긴급 분석을 트리거.

이 모듈은 "감지"만 담당 — 분석/판단/알림은 AgentHook + Orchestrator가 처리.

20x 레버리지 기준:
  BTC -1% = ROE -20%, -2.5% = ROE -50%, -5% = 청산(ROE -100%)

감지 기준:
  Level 1 (주의):   BTC 15분 -1.0% (= ROE -20%)
  Level 2 (경고):   BTC 1시간 -2.5% (= ROE -50%) → 긴급 에이전트 분석 트리거
  Level 3 (위험):   BTC 1시간 -3.5% (= ROE -70%) → 긴급 에이전트 분석 트리거
  Level 4 (크래시): BTC 4시간 -5.0% (= 청산 임박)  → 긴급 에이전트 분석 트리거
"""

from __future__ import annotations
import time
import numpy as np
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

# ── 레버리지 설정 ──
LEVERAGE = 20

# ── 감지 기준 (20x 레버리지 기준) ──
CRASH_LEVELS = {
    1: {"window": "15m", "candles": 1,  "threshold": -1.0,
        "roe_impact": -20, "cooldown": 900,
        "label": "급변 주의"},
    2: {"window": "1h",  "candles": 4,  "threshold": -2.5,
        "roe_impact": -50, "cooldown": 900,
        "label": "급락 경고"},
    3: {"window": "1h",  "candles": 4,  "threshold": -3.5,
        "roe_impact": -70, "cooldown": 300,
        "label": "즉시 대응"},
    4: {"window": "4h",  "candles": 16, "threshold": -5.0,
        "roe_impact": -100, "cooldown": 300,
        "label": "크래시 — 청산 임박"},
}

# 거래량 급증 배수 기준
VOLUME_SPIKE_MULTIPLIER = 3.0


class CrashDetector:
    """
    경량 시장 급락 감지기 (트리거 전용).

    역할: 급락 여부만 판단하고 결과를 반환.
    분석/판단/텔레그램 전송은 AgentHook이 담당.
    """

    def __init__(self, exchange=None):
        self.exchange = exchange
        self._cache_data: dict = {}
        self._cache_time: float = 0
        self._cache_ttl: float = 60
        self._last_alert: dict[int, float] = {}
        self._total_checks: int = 0
        self._total_alerts: int = 0

    def check(self) -> dict | None:
        """
        급락 여부 체크.

        반환:
        - None: 정상
        - dict: 급락 감지됨
          {level, label, change_pct, roe_impact, window, current_price,
           ref_price, volume_spike, multi_tf, remaining_to_liq}
        """
        self._total_checks += 1
        data = self._get_cached_data()
        if not data:
            return None

        prices = data.get("prices", [])
        volumes = data.get("volumes", [])
        if len(prices) < 17:
            return None

        now = time.time()

        for level in sorted(CRASH_LEVELS.keys(), reverse=True):
            cfg = CRASH_LEVELS[level]
            candles = cfg["candles"]
            if len(prices) <= candles:
                continue

            ref_price = prices[-(candles + 1)]
            change_pct = (prices[-1] - ref_price) / ref_price * 100

            if change_pct > cfg["threshold"]:
                continue

            cooldown = cfg.get("cooldown", 900)
            last = self._last_alert.get(level, 0)
            if now - last < cooldown:
                continue

            vol_spike = self._check_volume_spike(volumes)
            roe_impact = round(change_pct * LEVERAGE, 1)
            remaining_to_liq = round(5.0 + change_pct, 2)

            # 멀티타임프레임 정보
            multi_tf = {}
            for lv, lv_cfg in sorted(CRASH_LEVELS.items()):
                n = lv_cfg["candles"]
                if len(prices) > n:
                    ch = (prices[-1] - prices[-(n+1)]) / prices[-(n+1)] * 100
                    multi_tf[lv_cfg["window"]] = {
                        "change_pct": round(ch, 2),
                        "roe_impact": round(ch * LEVERAGE, 1),
                    }

            self._last_alert[level] = now
            for lower in range(1, level):
                self._last_alert[lower] = now
            self._total_alerts += 1

            return {
                "level": level,
                "label": cfg["label"],
                "change_pct": round(change_pct, 2),
                "roe_impact": roe_impact,
                "window": cfg["window"],
                "current_price": prices[-1],
                "ref_price": ref_price,
                "volume_spike": vol_spike,
                "multi_tf": multi_tf,
                "remaining_to_liq": remaining_to_liq,
                "timestamp": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST"),
            }

        return None

    def _get_cached_data(self) -> dict:
        """BTC 15분봉 데이터 (60초 캐시)"""
        now = time.time()
        if now - self._cache_time < self._cache_ttl and self._cache_data:
            return self._cache_data

        if not self.exchange:
            return {}

        try:
            ohlcv = self.exchange.fetch_ohlcv("BTC/USDT", "15m", limit=20)
            if not ohlcv:
                return self._cache_data
            self._cache_data = {
                "prices": [c[4] for c in ohlcv],
                "volumes": [c[5] for c in ohlcv],
                "timestamp": ohlcv[-1][0],
            }
            self._cache_time = now
        except Exception:
            pass

        return self._cache_data

    @staticmethod
    def _check_volume_spike(volumes: list) -> dict:
        if len(volumes) < 5:
            return {"spike": False, "ratio": 1.0}
        recent = volumes[-1]
        avg = float(np.mean(volumes[-13:-1]))
        ratio = recent / max(avg, 1e-10)
        return {"spike": ratio >= VOLUME_SPIKE_MULTIPLIER, "ratio": round(ratio, 1)}

    def get_stats(self) -> dict:
        return {
            "total_checks": self._total_checks,
            "total_alerts": self._total_alerts,
            "last_alerts": {
                lv: datetime.fromtimestamp(t, KST).strftime("%H:%M:%S")
                for lv, t in self._last_alert.items()
            },
        }
