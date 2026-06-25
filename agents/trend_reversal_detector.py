"""
trend_reversal_detector.py — BTC 방향 전환 감지기
===================================================
포지션 진입 후 BTC가 보합 → 반대 방향으로 꺾이는 상황 감지.
CrashDetector처럼 경량으로 v9 메인 루프(15초)에 편승.

CrashDetector와의 차이:
- CrashDetector: BTC 절대 하락폭 감지 (급락 전용)
- TrendReversalDetector: BTC 방향 전환 감지 (포지션 방향 대비)

감지 시나리오:
1. LONG 진입 → BTC 상승 → 보합 → 하락 전환 → 알림
2. SHORT 진입 → BTC 하락 → 보합 → 상승 전환 → 알림

사용하는 지표:
- EMA 5/15 (15분봉): 단기 추세 방향
- RSI 14: 모멘텀 전환
- 가격 vs 진입 시점 대비 변화
"""

from __future__ import annotations
import time
import numpy as np
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

# ── 설정 ──
EMA_FAST = 5        # 단기 EMA (15분봉 5개 = 75분)
EMA_SLOW = 15       # 중기 EMA (15분봉 15개 = 3.75시간)
RSI_PERIOD = 14
CANDLE_LIMIT = 30   # 필요 캔들 수

# 쿨다운: 같은 포지션에 대해 반복 알림 방지
ALERT_COOLDOWN = 1800  # 30분


def _ema(data: list[float], period: int) -> list[float]:
    """지수이동평균 계산"""
    if len(data) < period:
        return data[:]
    k = 2 / (period + 1)
    ema = [float(np.mean(data[:period]))]
    for price in data[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def _rsi(closes: list[float], period: int = 14) -> float:
    """RSI 계산 (최근 값)"""
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(d, 0) for d in deltas[-period:]]
    losses = [max(-d, 0) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


class TrendReversalDetector:
    """
    BTC 방향 전환 감지기.

    포지션 보유 중일 때만 활성화.
    BTC EMA 크로스 + RSI 전환으로 방향 변화 감지.
    """

    def __init__(self, exchange=None):
        self.exchange = exchange
        self._cache_data: dict = {}
        self._cache_time: float = 0
        self._cache_ttl: float = 60  # 60초 캐시 (CrashDetector와 공유 가능)
        self._last_alerts: dict[str, float] = {}  # {symbol: last_alert_time}
        self._prev_trend: str = ""  # 이전 추세 ("bullish", "bearish", "neutral")

    def check(self, positions: list) -> list[dict]:
        """
        포지션 방향과 BTC 추세 전환 비교.

        Args:
            positions: v9 Position 객체 리스트

        Returns:
            알림이 필요한 포지션 정보 리스트. 없으면 빈 리스트.
        """
        if not positions:
            return []

        data = self._get_btc_data()
        if not data:
            return []

        closes = data["closes"]
        if len(closes) < CANDLE_LIMIT:
            return []

        # EMA 계산
        ema_fast = _ema(closes, EMA_FAST)
        ema_slow = _ema(closes, EMA_SLOW)

        if not ema_fast or not ema_slow:
            return []

        # 현재 추세 판단
        current_trend = self._determine_trend(ema_fast, ema_slow, closes)
        rsi = _rsi(closes, RSI_PERIOD)

        # 추세 전환 감지
        trend_changed = (
            self._prev_trend != "" and
            self._prev_trend != current_trend and
            current_trend != "neutral"
        )

        self._prev_trend = current_trend

        if not trend_changed:
            return []

        # 포지션별 영향 평가
        now = time.time()
        alerts = []

        for pos in positions:
            symbol = getattr(pos, "symbol", "?")
            direction = getattr(pos, "direction", "long")

            # 쿨다운 체크
            last = self._last_alerts.get(symbol, 0)
            if now - last < ALERT_COOLDOWN:
                continue

            # 포지션 방향과 반대로 전환?
            adverse = (
                (direction == "long" and current_trend == "bearish") or
                (direction == "short" and current_trend == "bullish")
            )

            if not adverse:
                continue

            # 진입가 대비 현재 변화 — 해당 코인의 현재가 조회
            entry = getattr(pos, "entry_price", 0)
            current_price = 0
            try:
                if self.exchange:
                    ticker = self.exchange.fetch_ticker(symbol)
                    current_price = float(ticker.get("last", 0))
            except Exception:
                pass

            if entry and current_price:
                if direction == "long":
                    change_pct = (current_price - entry) / entry * 100
                else:
                    change_pct = (entry - current_price) / entry * 100
                roe_pct = change_pct * 20
            else:
                change_pct = 0
                roe_pct = 0

            self._last_alerts[symbol] = now

            alerts.append({
                "symbol": symbol,
                "direction": direction,
                "btc_trend": current_trend,
                "prev_trend": self._prev_trend if self._prev_trend != current_trend else "neutral",
                "rsi": round(rsi, 1),
                "btc_price": closes[-1],
                "ema_fast": round(ema_fast[-1], 1),
                "ema_slow": round(ema_slow[-1], 1),
                "pos_change_pct": round(change_pct, 2),
                "pos_roe_pct": round(roe_pct, 1),
                "hold_h": round(getattr(pos, "hold_h", lambda: 0)(), 1),
                "timestamp": datetime.now(KST).strftime("%H:%M KST"),
            })

        return alerts

    def _determine_trend(self, ema_fast: list, ema_slow: list,
                         closes: list) -> str:
        """
        EMA 크로스 + 기울기로 추세 판단.

        bullish: EMA5 > EMA15 + EMA5 상승 중
        bearish: EMA5 < EMA15 + EMA5 하락 중
        neutral: 보합 (EMA 수렴 중)
        """
        if len(ema_fast) < 3 or len(ema_slow) < 3:
            return "neutral"

        fast_now = ema_fast[-1]
        fast_prev = ema_fast[-3]  # 2캔들 전 (30분 전)
        slow_now = ema_slow[-1]

        # EMA 갭 비율
        gap_pct = (fast_now - slow_now) / slow_now * 100

        # EMA5 기울기 (상승/하락)
        slope = (fast_now - fast_prev) / fast_prev * 100

        # 판단
        if gap_pct > 0.05 and slope > 0.02:
            return "bullish"
        elif gap_pct < -0.05 and slope < -0.02:
            return "bearish"
        else:
            return "neutral"

    def _get_btc_data(self) -> dict:
        """BTC 15분봉 데이터 (60초 캐시)"""
        now = time.time()
        if now - self._cache_time < self._cache_ttl and self._cache_data:
            return self._cache_data

        if not self.exchange:
            return {}

        try:
            ohlcv = self.exchange.fetch_ohlcv("BTC/USDT", "15m", limit=CANDLE_LIMIT)
            if not ohlcv:
                return self._cache_data
            self._cache_data = {
                "closes": [c[4] for c in ohlcv],
                "volumes": [c[5] for c in ohlcv],
                "highs": [c[2] for c in ohlcv],
                "lows": [c[3] for c in ohlcv],
                "timestamp": ohlcv[-1][0],
            }
            self._cache_time = now
        except Exception as e:
            print(f"  [추세감지] BTC 데이터 수집 실패: {e}")

        return self._cache_data

    @staticmethod
    def format_alert(alert: dict) -> str:
        """단일 알림용 포맷 (하위 호환)"""
        return TrendReversalDetector.format_alerts([alert])

    @staticmethod
    def format_alerts(alerts: list[dict]) -> str:
        """여러 포지션을 하나의 알림으로 통합 포맷"""
        if not alerts:
            return ""
        first = alerts[0]
        trend_kr = "하락" if first["btc_trend"] == "bearish" else "상승"
        emoji = "🔻" if first["btc_trend"] == "bearish" else "🔺"

        lines = [
            f"{emoji} BTC 방향 전환 감지",
            f"{'━'*25}",
            f"BTC: {trend_kr} 전환 (RSI {first['rsi']})",
            f"  EMA5: ${first['ema_fast']:,.0f} | EMA15: ${first['ema_slow']:,.0f}",
            f"  현재가: ${first['btc_price']:,.0f}",
            f"\n⚠️ 영향 포지션 ({len(alerts)}개):",
        ]
        for a in alerts:
            direction_kr = "롱" if a["direction"] == "long" else "숏"
            roe = a["pos_roe_pct"]
            roe_emoji = "📈" if roe > 0 else "📉"
            lines.append(
                f"  {a['symbol']} ({direction_kr}) {a['hold_h']}h | "
                f"{roe_emoji} ROE {roe:+.1f}%"
            )
        lines.append(f"\n💡 BTC가 포지션 반대로 전환됨")
        lines.append(f"   SL 확인 및 포지션 점검 권고")
        lines.append(f"{'━'*25}")
        lines.append(f"⏰ {first['timestamp']}")
        return "\n".join(lines)
