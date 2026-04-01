"""
crash_detector.py — 실시간 시장 급락 감지 + 즉시 텔레그램 알림
================================================================
v9 메인 루프(15초)에 편승하여 경량 체크.
전체 에이전트 분석과 달리 BTC 가격만 빠르게 조회 → 즉시 알림.

20x 레버리지 기준 설계:
  BTC -1% = ROE -20%, -2.5% = ROE -50%, -5% = 청산(ROE -100%)
  → 청산 전에 충분히 대응할 수 있도록 기준을 공격적으로 설정

감지 기준 (OR 조건):
  Level 1 (주의):   BTC 15분 -1.0% (= ROE -20%) — 급변 초기 포착
  Level 2 (경고):   BTC 1시간 -2.5% (= ROE -50%) — SL/포지션 점검
  Level 3 (위험):   BTC 1시간 -3.5% (= ROE -70%) — 즉시 대응 필요
  Level 4 (크래시): BTC 4시간 -5.0% (= 청산 임박)  — 생존 모드
  추가: 거래량 급증 (최근 15분 거래량 > 평균의 3배)

쿨다운: Level 1~2는 15분, Level 3~4는 5분 (긴급도 반영)
"""

from __future__ import annotations
import time
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Any

KST = timezone(timedelta(hours=9))

# ── 레버리지 설정 ──
LEVERAGE = 20

# ── 감지 기준 (20x 레버리지 기준) ──
#
# ROE 환산: BTC 변동% × 20 = ROE%
#   -1.0% × 20 = ROE -20%  (자산 1/5 손실)
#   -2.5% × 20 = ROE -50%  (자산 반토막)
#   -3.5% × 20 = ROE -70%  (심각)
#   -5.0% × 20 = ROE -100% (청산)
#
CRASH_LEVELS = {
    1: {"window": "15m", "candles": 1,  "threshold": -1.0,
        "roe_impact": -20, "cooldown": 900,
        "emoji": "⚠️", "label": "급변 주의"},
    2: {"window": "1h",  "candles": 4,  "threshold": -2.5,
        "roe_impact": -50, "cooldown": 900,
        "emoji": "🔴", "label": "급락 경고"},
    3: {"window": "1h",  "candles": 4,  "threshold": -3.5,
        "roe_impact": -70, "cooldown": 300,
        "emoji": "🚨", "label": "즉시 대응"},
    4: {"window": "4h",  "candles": 16, "threshold": -5.0,
        "roe_impact": -100, "cooldown": 300,
        "emoji": "💀", "label": "크래시 — 청산 임박"},
}

# 거래량 급증 배수 기준
VOLUME_SPIKE_MULTIPLIER = 3.0


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
          {level, change_pct, window, volume_spike, roe_impact, message}
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

            # 레벨별 쿨다운 확인 (Level 3~4는 5분, Level 1~2는 15분)
            cooldown = cfg.get("cooldown", 900)
            last = self._last_alert.get(level, 0)
            if now - last < cooldown:
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
        """알림 메시지 구성 (20x 레버리지 ROE 영향 포함)"""
        ts = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S KST")
        emoji = cfg["emoji"]
        label = cfg["label"]
        window = cfg["window"]
        roe_impact = round(change_pct * LEVERAGE, 1)

        # 메시지 본문
        lines = [
            f"{emoji} [{label}] BTC 급락 감지",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"⏰ {ts}",
            f"",
            f"📉 {window} 변동: {change_pct:+.2f}%",
            f"💰 현재가: ${current:,.0f}",
            f"📍 기준가: ${ref_price:,.0f} ({window} 전)",
            f"",
            f"⚡ 20x 레버리지 ROE 영향: {roe_impact:+.1f}%",
        ]

        # 청산까지 남은 여유 (롱 기준)
        # 20x → 최대 역행 5%, 현재 이미 change_pct만큼 역행
        remaining_to_liq = 5.0 + change_pct  # change_pct는 음수
        if remaining_to_liq > 0:
            lines.append(f"🎯 롱 청산까지 잔여: {remaining_to_liq:.2f}% (${current * remaining_to_liq / 100:,.0f})")
        else:
            lines.append(f"💀 롱 진입가 기준 청산 구간 진입")

        # 거래량 정보
        if vol_spike["spike"]:
            lines.append(f"📊 거래량: 평균 대비 {vol_spike['ratio']:.1f}배 급증 ⚡")
        else:
            lines.append(f"📊 거래량: 평균 대비 {vol_spike['ratio']:.1f}배")

        # 레벨별 대응 가이드
        if level >= 4:
            lines.extend([
                f"",
                f"💀💀💀 크래시 — 청산 임박 💀💀💀",
                f"모든 롱 포지션 즉시 점검",
                f"SL 미설정 포지션은 수동 청산 검토",
                f"신규 진입 절대 금지",
            ])
        elif level >= 3:
            lines.extend([
                f"",
                f"🚨 즉시 대응 필요 (ROE {roe_impact:+.0f}%)",
                f"롱 포지션 SL → 본전 이동 또는 부분 청산",
                f"숏 포지션은 TP 점검 (반등 대비)",
                f"신규 진입 자제",
            ])
        elif level >= 2:
            lines.extend([
                f"",
                f"🔴 포지션 점검 필요 (ROE {roe_impact:+.0f}%)",
                f"롱 SL 점검, 포지션 사이즈 축소 검토",
                f"추가 하락 시 Level 3 (ROE -70%) 진입 가능",
            ])
        else:
            lines.extend([
                f"",
                f"⚠️ 단기 급변 감지 (ROE {roe_impact:+.0f}%)",
                f"추이 관찰, 연속 하락 시 Level 2 경고",
            ])

        # 멀티타임프레임 변동률 + ROE
        prices = self._cache_data.get("prices", [])
        if len(prices) >= 5:
            extras = []
            for lv, lv_cfg in sorted(CRASH_LEVELS.items()):
                n = lv_cfg["candles"]
                if len(prices) > n:
                    ch = (prices[-1] - prices[-(n+1)]) / prices[-(n+1)] * 100
                    roe = ch * LEVERAGE
                    marker = " ⬅️" if lv == level else ""
                    extras.append(f"  {lv_cfg['window']}: {ch:+.2f}% (ROE {roe:+.0f}%){marker}")
            if extras:
                lines.append(f"")
                lines.append(f"📈 멀티타임프레임:")
                lines.extend(extras)

        lines.append(f"━━━━━━━━━━━━━━━━━━━━")

        message = "\n".join(lines)

        return {
            "level": level,
            "change_pct": round(change_pct, 2),
            "roe_impact": roe_impact,
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
