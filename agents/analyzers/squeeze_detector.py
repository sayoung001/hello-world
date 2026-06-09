"""
squeeze_detector.py — 숏스퀴즈 / 롱 청산 캐스케이드 종료 감지기
================================================================
독립 모듈 (LLM 미사용). 매 루프 또는 명령어로 호출.

## 감지 원리

숏스퀴즈와 롱 캐스케이드는 모두 "연료(=청산 가능한 포지션)"로 구동된다.
연료가 소진되면 이벤트가 끝난다. 이 모듈은 연료 소진 징후를 다각도로 측정한다.

### 숏스퀴즈 종료 감지 (가격 급등 → 숏 청산 연쇄 → 상승 피로)
1. OI 급감 후 안정화 → 숏 포지션이 이미 대부분 청산됨
2. 펀딩비 극단적 양수 후 하락 전환 → 롱 과열 시그널
3. 볼륨 클라이맥스 후 감소 → 매수 에너지 소진
4. 가격 속도(ROC) 감속 → 상승 모멘텀 약화
5. RSI 과매수 + 다이버전스 → 가격 신고가인데 RSI는 하락

### 롱 캐스케이드 종료 감지 (가격 급락 → 롱 청산 연쇄 → 하락 피로)
1. OI 급감 후 안정화 → 롱 포지션이 이미 대부분 청산됨
2. 펀딩비 극단적 음수 후 회복 전환 → 숏 과열 시그널
3. 볼륨 클라이맥스(셀링 클라이맥스) 후 감소 → 매도 에너지 소진
4. 가격 속도(ROC) 감속 → 하락 모멘텀 약화
5. RSI 과매도 + 반등 → 바닥 시그널

## 상태 머신
IDLE → BUILDING → ACTIVE → EXHAUSTING → EXHAUSTED
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EventType(Enum):
    NONE = "none"
    SHORT_SQUEEZE = "short_squeeze"
    LONG_CASCADE = "long_cascade"


class EventPhase(Enum):
    IDLE = "idle"
    BUILDING = "building"
    ACTIVE = "active"
    EXHAUSTING = "exhausting"
    EXHAUSTED = "exhausted"


@dataclass
class ExhaustionSignal:
    """개별 소진 시그널"""
    name: str
    score: float          # 0~1 (1=완전 소진)
    weight: float         # 가중치
    detail: str           # 설명


@dataclass
class DetectionResult:
    """감지 결과"""
    event_type: EventType
    phase: EventPhase
    exhaustion_pct: float         # 종합 소진도 0~100%
    signals: list[ExhaustionSignal] = field(default_factory=list)
    confidence: float = 0.0       # 판단 신뢰도 0~1
    summary: str = ""
    raw_metrics: dict = field(default_factory=dict)


class SqueezeCascadeDetector:
    """
    숏스퀴즈 / 롱 청산 캐스케이드 종료 감지기

    사용법:
        detector = SqueezeCascadeDetector(exchange, coinglass_client)
        result = detector.detect("BTC")
    """

    # 감속 감지용 ROC 임계값
    ROC_EXTREME_UP = 3.0      # 4h봉 기준 3% 이상 = 급등
    ROC_EXTREME_DOWN = -3.0   # 4h봉 기준 -3% 이하 = 급락
    ROC_DECEL_RATIO = 0.4     # 최근 ROC가 피크의 40% 이하 = 감속

    # OI 변화 임계값
    OI_DROP_THRESHOLD = -5.0  # OI -5% 이상 감소 = 청산 진행 중
    OI_STABLE_RANGE = 1.5     # OI 변화 ±1.5% 이내 = 안정화

    # 펀딩비 임계값
    FUNDING_EXTREME_POS = 0.03   # 0.03% 이상 = 롱 과열
    FUNDING_EXTREME_NEG = -0.03  # -0.03% 이하 = 숏 과열

    # 볼륨 클라이맥스
    VOL_CLIMAX_MULT = 2.5     # 평균의 2.5배 = 클라이맥스
    VOL_DECLINE_RATIO = 0.5   # 클라이맥스 대비 50% 이하 = 감소

    def __init__(self, exchange=None, cg_client=None):
        if exchange is None:
            import ccxt
            exchange = ccxt.binance({
                "enableRateLimit": True,
                "options": {"defaultType": "future"}
            })
        self.exchange = exchange
        self.cg = cg_client

    def detect(self, symbol: str) -> DetectionResult:
        """
        종합 감지: 현재 이벤트 유형 + 상태 + 소진도 판정

        :param symbol: "BTC", "ETH" 등 (USDT 없이)
        :return: DetectionResult
        """
        pair = f"{symbol}/USDT"
        metrics = {}

        # ── 1. 가격 데이터 (4h봉 60개 = 10일) ──
        ohlcv = self._fetch_ohlcv(pair, "4h", 60)
        if ohlcv is None or len(ohlcv) < 20:
            return DetectionResult(
                event_type=EventType.NONE,
                phase=EventPhase.IDLE,
                exhaustion_pct=0,
                summary="데이터 부족"
            )

        close = ohlcv["close"].values
        high = ohlcv["high"].values
        low = ohlcv["low"].values
        volume = ohlcv["volume"].values
        current_price = float(close[-1])
        metrics["current_price"] = current_price

        # ROC (Rate of Change) — 가격 변화 속도
        roc_series = pd.Series(close).pct_change() * 100
        roc_values = roc_series.dropna().values
        metrics["roc_latest"] = float(roc_values[-1]) if len(roc_values) > 0 else 0
        metrics["roc_peak_up"] = float(np.max(roc_values[-12:])) if len(roc_values) >= 12 else 0
        metrics["roc_peak_down"] = float(np.min(roc_values[-12:])) if len(roc_values) >= 12 else 0

        # RSI 14
        delta = pd.Series(close).diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        rsi = (100 - 100 / (1 + rs)).values
        metrics["rsi"] = float(rsi[-1]) if not np.isnan(rsi[-1]) else 50
        metrics["rsi_prev"] = float(rsi[-2]) if len(rsi) > 1 and not np.isnan(rsi[-2]) else 50

        # 볼륨 분석
        vol_avg = float(np.mean(volume[-20:]))
        vol_latest = float(volume[-1])
        vol_peak = float(np.max(volume[-12:]))
        metrics["vol_avg"] = vol_avg
        metrics["vol_latest"] = vol_latest
        metrics["vol_peak_12"] = vol_peak
        metrics["vol_ratio"] = vol_latest / vol_avg if vol_avg > 0 else 1

        # 24h(6봉) 가격 변동률
        price_change_24h = (close[-1] / close[-7] - 1) * 100 if len(close) > 6 else 0
        metrics["price_change_24h"] = float(price_change_24h)

        # 48h(12봉) 가격 변동률
        price_change_48h = (close[-1] / close[-13] - 1) * 100 if len(close) > 12 else 0
        metrics["price_change_48h"] = float(price_change_48h)

        # ── 2. CoinGlass 데이터 ──
        oi_data = self._fetch_oi(symbol)
        funding_data = self._fetch_funding(symbol)
        liq_data = self._fetch_liquidation(symbol)
        metrics["oi"] = oi_data
        metrics["funding"] = funding_data
        metrics["liquidation"] = liq_data

        # ── 3. 이벤트 유형 판별 ──
        event_type = self._classify_event(metrics)
        if event_type == EventType.NONE:
            return DetectionResult(
                event_type=EventType.NONE,
                phase=EventPhase.IDLE,
                exhaustion_pct=0,
                summary="현재 스퀴즈/캐스케이드 미감지",
                raw_metrics=metrics,
                confidence=0.7
            )

        # ── 4. 소진도 채점 ──
        signals = self._score_exhaustion(event_type, metrics)
        total_weight = sum(s.weight for s in signals)
        weighted_score = sum(s.score * s.weight for s in signals)
        exhaustion_pct = (weighted_score / total_weight * 100) if total_weight > 0 else 0

        # ── 5. 상태 판정 ──
        phase = self._determine_phase(exhaustion_pct, metrics, event_type)
        confidence = min(0.95, 0.4 + len([s for s in signals if s.score > 0.5]) * 0.1)

        summary = self._build_summary(event_type, phase, exhaustion_pct, signals, metrics)

        return DetectionResult(
            event_type=event_type,
            phase=phase,
            exhaustion_pct=round(exhaustion_pct, 1),
            signals=signals,
            confidence=round(confidence, 2),
            summary=summary,
            raw_metrics=metrics
        )

    # ═══════════════════════════════════════════════════
    #  이벤트 분류
    # ═══════════════════════════════════════════════════

    def _classify_event(self, m: dict) -> EventType:
        """
        현재 숏스퀴즈 / 롱 캐스케이드가 진행 중인지 판별.

        핵심 기준:
        - 24~48h 내 급격한 가격 변동 (±5% 이상)
        - OI 급감 (청산 발생 증거)
        - 청산 데이터에서 한쪽 우위
        """
        price_24h = m.get("price_change_24h", 0)
        price_48h = m.get("price_change_48h", 0)
        oi = m.get("oi", {})
        liq = m.get("liquidation", {})
        funding = m.get("funding", {})

        oi_change = oi.get("change_pct", 0) if oi else 0
        liq_dominant = liq.get("dominant", "NONE") if liq else "NONE"
        funding_signal = funding.get("signal", "NEUTRAL") if funding else "NEUTRAL"

        squeeze_score = 0
        cascade_score = 0

        # 가격 기반
        if price_24h > 5 or price_48h > 8:
            squeeze_score += 2
        elif price_24h > 3 or price_48h > 5:
            squeeze_score += 1

        if price_24h < -5 or price_48h < -8:
            cascade_score += 2
        elif price_24h < -3 or price_48h < -5:
            cascade_score += 1

        # OI 급감 = 청산 발생
        if oi_change < -3:
            squeeze_score += 1
            cascade_score += 1

        # 청산 방향
        if liq_dominant == "SHORT_LIQUIDATION":
            squeeze_score += 2
        elif liq_dominant == "LONG_LIQUIDATION":
            cascade_score += 2

        # 펀딩비
        if funding_signal in ("LONG_OVERHEATED", "LONG_BIAS"):
            squeeze_score += 1
        elif funding_signal in ("SHORT_OVERHEATED", "SHORT_BIAS"):
            cascade_score += 1

        if squeeze_score >= 3 and squeeze_score > cascade_score:
            return EventType.SHORT_SQUEEZE
        elif cascade_score >= 3 and cascade_score > squeeze_score:
            return EventType.LONG_CASCADE
        return EventType.NONE

    # ═══════════════════════════════════════════════════
    #  소진도 채점 (6가지 시그널)
    # ═══════════════════════════════════════════════════

    def _score_exhaustion(self, event_type: EventType,
                          m: dict) -> list[ExhaustionSignal]:
        """
        이벤트 종류에 따라 소진 시그널 6개를 채점.
        각 시그널의 score는 0(진행 중)~1(완전 소진).
        """
        signals = []

        if event_type == EventType.SHORT_SQUEEZE:
            signals.append(self._sig_roc_decel(m, direction="up"))
            signals.append(self._sig_oi_stabilize(m))
            signals.append(self._sig_vol_decline(m))
            signals.append(self._sig_rsi_extreme(m, direction="overbought"))
            signals.append(self._sig_funding_reversal(m, direction="positive"))
            signals.append(self._sig_liq_decline(m, liq_side="short"))
        else:  # LONG_CASCADE
            signals.append(self._sig_roc_decel(m, direction="down"))
            signals.append(self._sig_oi_stabilize(m))
            signals.append(self._sig_vol_decline(m))
            signals.append(self._sig_rsi_extreme(m, direction="oversold"))
            signals.append(self._sig_funding_reversal(m, direction="negative"))
            signals.append(self._sig_liq_decline(m, liq_side="long"))

        return signals

    def _sig_roc_decel(self, m: dict, direction: str) -> ExhaustionSignal:
        """
        시그널 1: 가격 속도 감속

        근거: 스퀴즈/캐스케이드의 핵심 동력은 연쇄 청산.
        청산 물량이 줄면 가격 이동 속도가 먼저 감소한다.
        ROC(Rate of Change)의 피크 대비 현재값 비율로 감속 정도를 측정.
        """
        roc_latest = abs(m.get("roc_latest", 0))
        if direction == "up":
            roc_peak = abs(m.get("roc_peak_up", 0.01))
        else:
            roc_peak = abs(m.get("roc_peak_down", 0.01))

        if roc_peak < 0.5:
            return ExhaustionSignal("가격속도 감속", 0.0, 2.0,
                                    "변동 미미 — 이벤트 아닐 수 있음")

        decel_ratio = 1.0 - (roc_latest / roc_peak) if roc_peak > 0 else 0
        score = np.clip(decel_ratio, 0, 1)

        if score > 0.7:
            detail = f"강한 감속 (ROC {roc_latest:.1f}% ← 피크 {roc_peak:.1f}%)"
        elif score > 0.3:
            detail = f"감속 진행 (ROC {roc_latest:.1f}% ← 피크 {roc_peak:.1f}%)"
        else:
            detail = f"아직 강한 모멘텀 (ROC {roc_latest:.1f}%)"

        return ExhaustionSignal("가격속도 감속", round(float(score), 3), 2.0, detail)

    def _sig_oi_stabilize(self, m: dict) -> ExhaustionSignal:
        """
        시그널 2: OI 안정화

        근거: OI(미결제약정)는 시장에 남아있는 '연료' 총량.
        스퀴즈/캐스케이드 중 OI가 급감하다가 변화율이 줄면
        → 청산할 포지션이 거의 소진되었다는 의미.
        OI 변화율의 절대값이 작아지면 안정화로 판단.
        """
        oi = m.get("oi", {})
        if not oi:
            return ExhaustionSignal("OI 안정화", 0.5, 2.5,
                                    "OI 데이터 없음 (중립 처리)")

        oi_change = abs(oi.get("change_pct", 0))
        oi_trend = oi.get("trend", "FLAT")

        if oi_trend == "FLAT" or oi_change < self.OI_STABLE_RANGE:
            score = 0.9
            detail = f"OI 안정화됨 ({oi.get('change_pct', 0):+.1f}%)"
        elif oi_change < abs(self.OI_DROP_THRESHOLD):
            ratio = 1.0 - (oi_change / abs(self.OI_DROP_THRESHOLD))
            score = np.clip(ratio, 0.2, 0.7)
            detail = f"OI 감소 둔화 ({oi.get('change_pct', 0):+.1f}%)"
        else:
            score = 0.1
            detail = f"OI 여전히 급감 ({oi.get('change_pct', 0):+.1f}%) → 청산 진행 중"

        return ExhaustionSignal("OI 안정화", round(float(score), 3), 2.5, detail)

    def _sig_vol_decline(self, m: dict) -> ExhaustionSignal:
        """
        시그널 3: 거래량 감소 (볼륨 클라이맥스 이후)

        근거: 스퀴즈/캐스케이드 초기에는 강제 청산으로 거래량이 폭증한다.
        이를 '볼륨 클라이맥스'라 한다. 클라이맥스 이후 거래량이 줄면
        → 강제 매매가 소진되었다는 의미.
        최근 12봉 중 피크 볼륨 대비 최신 볼륨의 비율로 판단.
        """
        vol_latest = m.get("vol_latest", 0)
        vol_peak = m.get("vol_peak_12", 0)
        vol_avg = m.get("vol_avg", 1)

        had_climax = vol_peak > vol_avg * self.VOL_CLIMAX_MULT
        if not had_climax:
            return ExhaustionSignal("볼륨 감소", 0.3, 1.5,
                                    f"볼륨 클라이맥스 미발생 (피크 {vol_peak/vol_avg:.1f}x)")

        decline_ratio = 1.0 - (vol_latest / vol_peak) if vol_peak > 0 else 0
        score = np.clip(decline_ratio, 0, 1)

        if score > 0.6:
            detail = f"볼륨 급감 (현재 {vol_latest/vol_avg:.1f}x ← 피크 {vol_peak/vol_avg:.1f}x)"
        else:
            detail = f"볼륨 아직 높음 (현재 {vol_latest/vol_avg:.1f}x, 피크 {vol_peak/vol_avg:.1f}x)"

        return ExhaustionSignal("볼륨 감소", round(float(score), 3), 1.5, detail)

    def _sig_rsi_extreme(self, m: dict, direction: str) -> ExhaustionSignal:
        """
        시그널 4: RSI 극단값 + 다이버전스

        근거:
        - 숏스퀴즈: RSI > 75 = 과매수. RSI가 하락 전환하면 매수 에너지 소진.
        - 롱캐스케이드: RSI < 25 = 과매도. RSI가 상승 전환하면 매도 에너지 소진.
        RSI가 극단에서 반전할 때 소진 점수가 높아진다.
        """
        rsi = m.get("rsi", 50)
        rsi_prev = m.get("rsi_prev", 50)

        if direction == "overbought":
            if rsi > 80 and rsi < rsi_prev:
                score = 0.9
                detail = f"RSI {rsi:.0f} 과매수에서 하락 전환 (이전 {rsi_prev:.0f})"
            elif rsi > 75:
                score = 0.5
                detail = f"RSI {rsi:.0f} 과매수 진입 (아직 하락 전환 아님)"
            elif rsi > 65:
                score = 0.2
                detail = f"RSI {rsi:.0f} 높지만 극단 아님"
            else:
                score = 0.0
                detail = f"RSI {rsi:.0f} 정상 범위"
        else:  # oversold
            if rsi < 20 and rsi > rsi_prev:
                score = 0.9
                detail = f"RSI {rsi:.0f} 과매도에서 반등 (이전 {rsi_prev:.0f})"
            elif rsi < 25:
                score = 0.5
                detail = f"RSI {rsi:.0f} 과매도 진입 (아직 반등 아님)"
            elif rsi < 35:
                score = 0.2
                detail = f"RSI {rsi:.0f} 낮지만 극단 아님"
            else:
                score = 0.0
                detail = f"RSI {rsi:.0f} 정상 범위"

        return ExhaustionSignal("RSI 극단", round(score, 3), 1.5, detail)

    def _sig_funding_reversal(self, m: dict, direction: str) -> ExhaustionSignal:
        """
        시그널 5: 펀딩비 극단 후 반전

        근거:
        - 숏스퀴즈 중: 펀딩비 급등(양수) → 롱이 과열.
          펀딩비가 정점 찍고 하락하면 = 롱 진입 둔화 = 스퀴즈 동력 약화.
        - 롱캐스케이드 중: 펀딩비 급락(음수) → 숏이 과열.
          펀딩비가 저점 찍고 상승하면 = 숏 진입 둔화 = 캐스케이드 동력 약화.

        펀딩비는 8시간마다 정산되므로 방향 전환이 명확하면 강한 시그널.
        """
        funding = m.get("funding", {})
        if not funding:
            return ExhaustionSignal("펀딩비 반전", 0.5, 2.0,
                                    "펀딩비 데이터 없음 (중립 처리)")

        rates = funding.get("rates", [])
        signal = funding.get("signal", "NEUTRAL")
        current = funding.get("current", 0)

        if len(rates) < 2:
            return ExhaustionSignal("펀딩비 반전", 0.3, 2.0, "펀딩비 히스토리 부족")

        if direction == "positive":  # 숏스퀴즈 — 양수 펀딩비 반전 감시
            if current > self.FUNDING_EXTREME_POS and rates[-1] < rates[-2]:
                score = 0.85
                detail = f"펀딩비 {current*100:.4f}% 극단 양수 + 하락 전환"
            elif current > self.FUNDING_EXTREME_POS:
                score = 0.4
                detail = f"펀딩비 {current*100:.4f}% 극단 양수 (아직 상승 중)"
            elif signal == "LONG_OVERHEATED":
                score = 0.6
                detail = f"펀딩비 롱 과열 상태 ({funding.get('rates_str', '')})"
            else:
                score = 0.1
                detail = f"펀딩비 정상 ({current*100:.4f}%)"
        else:  # 롱캐스케이드 — 음수 펀딩비 반전 감시
            if current < self.FUNDING_EXTREME_NEG and rates[-1] > rates[-2]:
                score = 0.85
                detail = f"펀딩비 {current*100:.4f}% 극단 음수 + 반등 전환"
            elif current < self.FUNDING_EXTREME_NEG:
                score = 0.4
                detail = f"펀딩비 {current*100:.4f}% 극단 음수 (아직 하락 중)"
            elif signal == "SHORT_OVERHEATED":
                score = 0.6
                detail = f"펀딩비 숏 과열 상태 ({funding.get('rates_str', '')})"
            else:
                score = 0.1
                detail = f"펀딩비 정상 ({current*100:.4f}%)"

        return ExhaustionSignal("펀딩비 반전", round(score, 3), 2.0, detail)

    def _sig_liq_decline(self, m: dict, liq_side: str) -> ExhaustionSignal:
        """
        시그널 6: 청산 물량 감소

        근거: 스퀴즈/캐스케이드의 직접적인 연료는 '청산'.
        - 숏스퀴즈: 숏 청산 물량이 줄면 → 스퀴즈 연료 소진
        - 롱캐스케이드: 롱 청산 물량이 줄면 → 캐스케이드 연료 소진
        청산 물량 자체가 줄어드는 것이 가장 직접적인 종료 시그널.
        """
        liq = m.get("liquidation", {})
        if not liq:
            return ExhaustionSignal("청산 감소", 0.5, 2.5,
                                    "청산 데이터 없음 (중립 처리)")

        dominant = liq.get("dominant", "NONE")
        total = liq.get("total_usd", 0)
        long_liq = liq.get("long_liquidation_usd", 0)
        short_liq = liq.get("short_liquidation_usd", 0)

        if liq_side == "short":  # 숏스퀴즈 — 숏 청산 감소 확인
            if dominant == "BALANCED" or dominant == "NONE":
                score = 0.8
                detail = f"숏 청산 소진 (롱${long_liq:,.0f} / 숏${short_liq:,.0f})"
            elif dominant == "LONG_LIQUIDATION":
                score = 0.95
                detail = f"반전! 롱 청산 우위로 전환 (스퀴즈 종료 강력 시그널)"
            elif total < 100_000:
                score = 0.7
                detail = f"청산 물량 미미 (${total:,.0f}) — 연료 소진"
            else:
                score = 0.2
                detail = f"숏 청산 여전히 진행 (숏${short_liq:,.0f})"
        else:  # 롱캐스케이드 — 롱 청산 감소 확인
            if dominant == "BALANCED" or dominant == "NONE":
                score = 0.8
                detail = f"롱 청산 소진 (롱${long_liq:,.0f} / 숏${short_liq:,.0f})"
            elif dominant == "SHORT_LIQUIDATION":
                score = 0.95
                detail = f"반전! 숏 청산 우위로 전환 (캐스케이드 종료 강력 시그널)"
            elif total < 100_000:
                score = 0.7
                detail = f"청산 물량 미미 (${total:,.0f}) — 연료 소진"
            else:
                score = 0.2
                detail = f"롱 청산 여전히 진행 (롱${long_liq:,.0f})"

        return ExhaustionSignal("청산 감소", round(score, 3), 2.5, detail)

    # ═══════════════════════════════════════════════════
    #  상태 판정
    # ═══════════════════════════════════════════════════

    def _determine_phase(self, exhaustion_pct: float,
                         m: dict, event_type: EventType) -> EventPhase:
        """
        소진도와 원시 지표로 현재 상태를 판정.

        BUILDING:   이벤트 초기 (소진 0~20%)
        ACTIVE:     이벤트 진행 중 (소진 20~50%)
        EXHAUSTING: 소진 진행 (50~75%)
        EXHAUSTED:  사실상 종료 (75%+)
        """
        if exhaustion_pct >= 75:
            return EventPhase.EXHAUSTED
        elif exhaustion_pct >= 50:
            return EventPhase.EXHAUSTING
        elif exhaustion_pct >= 20:
            return EventPhase.ACTIVE
        else:
            return EventPhase.BUILDING

    # ═══════════════════════════════════════════════════
    #  데이터 수집 헬퍼
    # ═══════════════════════════════════════════════════

    def _fetch_ohlcv(self, pair: str, tf: str, limit: int) -> Optional[pd.DataFrame]:
        try:
            raw = self.exchange.fetch_ohlcv(pair, timeframe=tf, limit=limit)
            df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
            return df
        except Exception:
            return None

    def _fetch_oi(self, symbol: str) -> dict:
        if not self.cg:
            return {}
        try:
            return self.cg.get_oi_change(symbol, "1h") or {}
        except Exception:
            return {}

    def _fetch_funding(self, symbol: str) -> dict:
        if not self.cg:
            return {}
        try:
            return self.cg.get_funding_rate_trend(symbol) or {}
        except Exception:
            return {}

    def _fetch_liquidation(self, symbol: str) -> dict:
        if not self.cg:
            return {}
        try:
            return self.cg.get_recent_liquidation_pressure(symbol) or {}
        except Exception:
            return {}

    # ═══════════════════════════════════════════════════
    #  출력 포맷
    # ═══════════════════════════════════════════════════

    def _build_summary(self, event_type: EventType, phase: EventPhase,
                       exhaustion_pct: float, signals: list[ExhaustionSignal],
                       m: dict) -> str:
        type_name = "숏스퀴즈" if event_type == EventType.SHORT_SQUEEZE else "롱 캐스케이드"
        phase_name = {
            EventPhase.BUILDING: "초기 진행",
            EventPhase.ACTIVE: "활발 진행",
            EventPhase.EXHAUSTING: "소진 진행",
            EventPhase.EXHAUSTED: "사실상 종료",
        }[phase]

        strong = [s for s in signals if s.score >= 0.7]
        weak = [s for s in signals if s.score < 0.3]

        lines = [f"{type_name} → {phase_name} (소진도 {exhaustion_pct:.0f}%)"]
        if strong:
            lines.append(f"종료 근거: {', '.join(s.name for s in strong)}")
        if weak:
            lines.append(f"아직 진행: {', '.join(s.name for s in weak)}")
        return " | ".join(lines)

    def format_telegram(self, result: DetectionResult) -> str:
        """텔레그램 메시지 포맷팅"""
        if result.event_type == EventType.NONE:
            return "📊 스퀴즈/캐스케이드 감지: 없음\n현재 정상 시장 상태입니다."

        type_emoji = "🟢⬆️" if result.event_type == EventType.SHORT_SQUEEZE else "🔴⬇️"
        type_name = "숏스퀴즈" if result.event_type == EventType.SHORT_SQUEEZE else "롱 캐스케이드"

        phase_emoji = {
            EventPhase.BUILDING: "🔵",
            EventPhase.ACTIVE: "🟠",
            EventPhase.EXHAUSTING: "🟡",
            EventPhase.EXHAUSTED: "✅",
        }[result.phase]

        phase_name = {
            EventPhase.BUILDING: "초기 진행",
            EventPhase.ACTIVE: "활발 진행",
            EventPhase.EXHAUSTING: "⚠️ 소진 진행",
            EventPhase.EXHAUSTED: "🏁 사실상 종료",
        }[result.phase]

        bar_len = 20
        filled = int(result.exhaustion_pct / 100 * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)

        lines = [
            f"{type_emoji} {type_name} 감지",
            f"{'━' * 24}",
            f"상태: {phase_emoji} {phase_name}",
            f"소진도: [{bar}] {result.exhaustion_pct:.0f}%",
            f"신뢰도: {result.confidence:.0%}",
            "",
            "📋 소진 시그널:",
        ]

        for s in sorted(result.signals, key=lambda x: x.score, reverse=True):
            if s.score >= 0.7:
                emoji = "✅"
            elif s.score >= 0.4:
                emoji = "🟡"
            else:
                emoji = "🔴"
            lines.append(f"  {emoji} {s.name} ({s.score:.0%}): {s.detail}")

        m = result.raw_metrics
        price = m.get("current_price", 0)
        p24 = m.get("price_change_24h", 0)
        rsi = m.get("rsi", 0)
        lines.extend([
            "",
            f"💰 현재가: {price:,.4f} (24h: {p24:+.1f}%)",
            f"📈 RSI: {rsi:.0f}",
        ])

        oi = m.get("oi", {})
        if oi:
            lines.append(f"📊 OI: {oi.get('change_pct', 0):+.1f}% ({oi.get('trend', 'N/A')})")

        if result.phase == EventPhase.EXHAUSTED:
            if result.event_type == EventType.SHORT_SQUEEZE:
                lines.append("\n⚠️ 스퀴즈 종료 임박 → 숏 진입 기회 탐색 구간")
            else:
                lines.append("\n⚠️ 캐스케이드 종료 임박 → 롱 반등 기회 탐색 구간")
        elif result.phase == EventPhase.EXHAUSTING:
            lines.append("\n💡 소진 진행 중 — 포지션 축소 또는 반대 포지션 준비 권장")

        return "\n".join(lines)
