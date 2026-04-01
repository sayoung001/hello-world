"""
crash_detector.py — 실시간 시장 급락 감지 + 즉시 텔레그램 알림
================================================================
v9 메인 루프(15초)에 편승하여 경량 체크.
전체 에이전트 분석과 달리 BTC 가격만 빠르게 조회 → 즉시 알림.

감지 기준 (OR 조건):
  Level 1 (경고):   BTC 15분 -2% 이상 하락
  Level 2 (위험):   BTC 1시간 -5% 이상 하락
  Level 3 (크래시): BTC 4시간 -10% 이상 하락
  추가: 거래량 급증 (최근 15분 거래량 > 평균의 3배)

쿨다운: 동일 레벨 알림은 15분 간격으로 제한 (스팸 방지)
"""

from __future__ import annotations
import time
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Any

KST = timezone(timedelta(hours=9))

# ── 감지 기준 ──
CRASH_LEVELS = {
    1: {"window": "15m", "candles": 1,  "threshold": -2.0,
        "emoji": "⚠️", "label": "급락 경고"},
    2: {"window": "1h",  "candles": 4,  "threshold": -5.0,
        "emoji": "🔴", "label": "시장 위험"},
    3: {"window": "4h",  "candles": 16, "threshold": -10.0,
        "emoji": "🚨", "label": "크래시 감지"},
}

# 거래량 급증 배수 기준
VOLUME_SPIKE_MULTIPLIER = 3.0

# 쿨다운 (초)
COOLDOWN_SEC = 900  # 15분


class CrashDetector:
    """
    경량 시장 급락 감지기.

    설계 원칙:
    - API 호출 최소화 (BTC 1종목, 15분봉 1회)
    - 15초마다 호출되어도 안전 (내부 캐싱 60초)
    - 텔레그램 즉시 발송 (4시간 주기 아님)
    - 쿨다운으로 알림 스팸 방지
    """

    def __init__(self, exchange=None, tg=None):
        """
        :param exchange: ccxt Binance 인스턴스 (v9에서 전달)
        :param tg: 텔레그램 인스턴스 (.send() 메서드)
        """
        self.exchange = exchange
        self.tg = tg

        # 캐시: API 호출 최소화 (60초 유지)
        self._cache_data: dict = {}
        self._cache_time: float = 0
        self._cache_ttl: float = 60  # 60초 캐시

        # 쿨다운: {level: last_alert_time}
        self._last_alert: dict[int, float] = {}

        # 통계
        self._total_checks: int = 0
        self._total_alerts: int = 0

    def check(self) -> dict | None:
        """
        급락 여부 체크. v9 메인 루프에서 매 사이클 호출.

        반환:
        - None: 정상 (급락 없음)
        - dict: 급락 감지됨 → 텔레그램 발송 완료 후 결과 반환
          {level, change_pct, window, volume_spike, message}
        """
        self._total_checks += 1
        data = self._get_cached_data()
        if not data:
            return None

        prices = data.get("prices", [])
        volumes = data.get("volumes", [])
        if len(prices) < 17:  # 최소 4h (16캔들) + 현재
            return None

        current = prices[-1]
        now = time.time()

        # 각 레벨 체크 (높은 레벨부터 → 중복 알림 방지)
        for level in sorted(CRASH_LEVELS.keys(), reverse=True):
            cfg = CRASH_LEVELS[level]
            candles = cfg["candles"]

            if len(prices) <= candles:
                continue

            ref_price = prices[-(candles + 1)]
            change_pct = (current - ref_price) / ref_price * 100

            if change_pct > cfg["threshold"]:
                continue  # 이 레벨은 정상

            # 쿨다운 확인
            last = self._last_alert.get(level, 0)
            if now - last < COOLDOWN_SEC:
                continue

            # 거래량 스파이크 체크
            vol_spike = self._check_volume_spike(volumes)

            # 알림 발송
            alert = self._build_alert(level, change_pct, current, ref_price,
                                       cfg, vol_spike, volumes)
            self._send_alert(alert)
            self._last_alert[level] = now
            # 상위 레벨 알림 시 하위도 쿨다운 갱신
            for lower in range(1, level):
                self._last_alert[lower] = now

            self._total_alerts += 1
            return alert

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
                return self._cache_data  # 실패 시 이전 캐시 유지

            prices = [c[4] for c in ohlcv]   # close
            volumes = [c[5] for c in ohlcv]   # volume

            self._cache_data = {
                "prices": prices,
                "volumes": volumes,
                "timestamp": ohlcv[-1][0],
            }
            self._cache_time = now
        except Exception:
            pass  # 네트워크 오류 시 이전 캐시 유지

        return self._cache_data

    @staticmethod
    def _check_volume_spike(volumes: list) -> dict:
        """최근 15분 거래량 vs 평균 비교"""
        if len(volumes) < 5:
            return {"spike": False, "ratio": 1.0}

        recent = volumes[-1]
        avg = float(np.mean(volumes[-13:-1]))  # 최근 12캔들 (3시간) 평균
        ratio = recent / max(avg, 1e-10)

        return {
            "spike": ratio >= VOLUME_SPIKE_MULTIPLIER,
            "ratio": round(ratio, 1),
        }

    def _build_alert(self, level: int, change_pct: float, current: float,
                      ref_price: float, cfg: dict, vol_spike: dict,
                      volumes: list) -> dict:
        """알림 메시지 구성"""
        ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        emoji = cfg["emoji"]
        label = cfg["label"]
        window = cfg["window"]

        # 메시지 본문
        lines = [
            f"{emoji} [{label}] BTC 급락 감지",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"⏰ {ts}",
            f"",
            f"📉 {window} 변동: {change_pct:+.2f}%",
            f"💰 현재가: ${current:,.0f}",
            f"📍 기준가: ${ref_price:,.0f} ({window} 전)",
        ]

        # 거래량 정보
        if vol_spike["spike"]:
            lines.append(f"📊 거래량: 평균 대비 {vol_spike['ratio']:.1f}배 급증 ⚡")
        else:
            lines.append(f"📊 거래량: 평균 대비 {vol_spike['ratio']:.1f}배")

        # 레벨별 추가 정보
        if level >= 3:
            lines.extend([
                f"",
                f"🚨🚨🚨 크래시 수준 급락 🚨🚨🚨",
                f"모든 포지션 즉시 점검 필요",
                f"레버리지 20x 기준 청산 위험 극대",
            ])
        elif level >= 2:
            lines.extend([
                f"",
                f"🔴 시장 위험 수준 — 포지션 점검 권고",
                f"롱 포지션 SL 점검, 신규 진입 자제",
            ])
        else:
            lines.extend([
                f"",
                f"⚠️ 단기 급락 — 추이 관찰 필요",
            ])

        # 추가 컨텍스트: 여러 타임프레임 변동률
        prices = self._cache_data.get("prices", [])
        if len(prices) >= 5:
            extras = []
            for lv, lv_cfg in sorted(CRASH_LEVELS.items()):
                n = lv_cfg["candles"]
                if len(prices) > n:
                    ch = (prices[-1] - prices[-(n+1)]) / prices[-(n+1)] * 100
                    extras.append(f"  {lv_cfg['window']}: {ch:+.2f}%")
            if extras:
                lines.append(f"")
                lines.append(f"📈 멀티타임프레임:")
                lines.extend(extras)

        lines.append(f"━━━━━━━━━━━━━━━━━━━━")

        message = "\n".join(lines)

        return {
            "level": level,
            "change_pct": round(change_pct, 2),
            "window": window,
            "current_price": current,
            "ref_price": ref_price,
            "volume_spike": vol_spike,
            "message": message,
        }

    def _send_alert(self, alert: dict):
        """텔레그램 즉시 발송"""
        msg = alert["message"]
        print(f"\n{msg}\n")

        if self.tg:
            try:
                self.tg.send(msg)
            except Exception as e:
                print(f"  ❌ Crash alert 전송 실패: {e}")

    def get_stats(self) -> dict:
        """감지기 통계"""
        return {
            "total_checks": self._total_checks,
            "total_alerts": self._total_alerts,
            "last_alerts": {
                lv: datetime.fromtimestamp(t, KST).strftime("%H:%M:%S")
                for lv, t in self._last_alert.items()
            },
            "cache_age_sec": round(time.time() - self._cache_time, 0)
                if self._cache_time else None,
        }
