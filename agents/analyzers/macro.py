"""
macro.py — Agent 2: 매크로 분석가
==================================
전통 금융시장 및 거시경제 환경 분석.
분석 유형: Subjective (주관적 해석)

데이터 소스:
- CoinGlass 공포탐욕지수
- DXY (달러 인덱스) 프록시: EURUSD 역수 from Binance
- VIX 프록시: BTC 30일 실현 변동성
- 경제 캘린더: 주요 이벤트 룰 기반 스케줄
- BTC 일봉 변동성 분석
"""

from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Any

from agents.core.base import AgentBase
from agents.core.protocol import AgentMessage

KST = timezone(timedelta(hours=9))

# ── 주요 경제 이벤트 캘린더 (반복 스케줄) ──
# 매월 고정 패턴 + 알려진 FOMC 일정
RECURRING_EVENTS = [
    # (day_of_month_approx, event, impact, note)
    (1, "ISM 제조업 PMI", "medium", "50 미만=수축"),
    (3, "ISM 비제조업 PMI", "medium", "서비스업 경기"),
    (6, "고용보고서(NFP)", "high", "첫째주 금요일, 변동성 최대"),
    (10, "CPI 발표", "high", "인플레이션 핵심 지표"),
    (13, "PPI 발표", "medium", "생산자 물가"),
    (15, "소매판매", "medium", "소비 동향"),
    (20, "FOMC 회의", "high", "연 8회, 금리 결정"),
    (27, "PCE 물가지수", "high", "연준 선호 인플레 지표"),
    (28, "GDP 발표", "medium", "분기별"),
]

# 2026년 FOMC 일정 (예정)
FOMC_DATES_2026 = [
    (1, 28), (3, 18), (5, 6), (6, 17),
    (7, 29), (9, 16), (11, 4), (12, 16),
]


class MacroAgent(AgentBase):
    """
    매크로 분석가

    책임:
    - 당일/익일 주요 경제 이벤트 파악
    - Risk-on vs Risk-off 환경 판단
    - DXY 방향성과 BTC 역상관 확인
    - VIX(변동성) 레벨 판단
    - 공포탐욕지수 기반 시장 과열/공포 판단
    """

    def __init__(self, cg_client=None, exchange=None):
        super().__init__(
            agent_id="agent_2_macro",
            agent_name="매크로 분석가",
            role_description="전통 금융시장 및 거시경제 환경 분석 (Subjective)"
        )
        self._analysis_type = "subjective"
        self.cg = cg_client
        self._exchange = exchange

    def _get_exchange(self):
        if self._exchange is None:
            import ccxt
            self._exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "future"}})
        return self._exchange

    def _get_system_prompt(self) -> str:
        return """당신은 크립토 트레이딩을 위한 거시경제 분석가입니다.
전통 금융시장(S&P500, DXY, VIX)이 크립토에 미치는 영향을 분석합니다.

분석 원칙:
- 20배 레버리지 트레이딩 환경에서의 리스크 평가
- 고임팩트 경제 이벤트(FOMC, CPI 등)는 최우선 고려
- 불확실하면 defensive 권고 (생존 우선)

반드시 JSON 형식으로 출력:
```json
{
  "risk_appetite": "risk-on|neutral|risk-off",
  "upcoming_events": [{"event": "이벤트명", "impact": "high|medium|low", "days_until": N, "note": "요약"}],
  "dxy_trend": "bullish|bearish|neutral",
  "dxy_change_7d": 변동률%,
  "vix_level": "low|moderate|high|extreme",
  "realized_vol_30d": 연환산변동성%,
  "fear_greed": {"value": 0~100, "label": "라벨"},
  "equity_sentiment": "bullish|neutral|bearish",
  "recommendation": "aggressive|neutral|defensive",
  "risk_level": "SAFE|CAUTION|DANGER",
  "confidence": 0.0~1.0,
  "reasoning": "분석 근거"
}
```"""

    # ── 경제 캘린더 파싱 ──

    @staticmethod
    def _get_upcoming_events(now: datetime = None) -> list[dict]:
        """향후 3일 내 주요 경제 이벤트 조회"""
        if now is None:
            now = datetime.now(KST)
        today = now.day
        month = now.month

        events = []

        # FOMC 체크 (정확한 날짜)
        for m, d in FOMC_DATES_2026:
            if m == month and 0 <= d - today <= 3:
                events.append({
                    "event": "FOMC 금리 결정",
                    "impact": "high",
                    "days_until": d - today,
                    "note": "변동성 최대 이벤트, 20x 레버리지 주의"
                })

        # 반복 이벤트 체크
        for approx_day, name, impact, note in RECURRING_EVENTS:
            if name == "FOMC 회의":
                continue  # 위에서 정확한 날짜로 처리
            diff = approx_day - today
            # 이번 달 내 ±2일 오차 허용
            if -1 <= diff <= 3:
                events.append({
                    "event": name,
                    "impact": impact,
                    "days_until": max(diff, 0),
                    "note": note,
                })

        # 월말/월초 리밸런싱
        import calendar
        _, last_day = calendar.monthrange(now.year, month)
        if last_day - today <= 2:
            events.append({
                "event": "월말 리밸런싱",
                "impact": "medium",
                "days_until": last_day - today,
                "note": "기관 포트폴리오 리밸런싱 — 변동성 증가 가능",
            })

        # 분기말
        if month in (3, 6, 9, 12) and last_day - today <= 3:
            events.append({
                "event": "분기말 결산",
                "impact": "high",
                "days_until": last_day - today,
                "note": "분기 결산 + 옵션 만기 — 변동성 극대화",
            })

        return sorted(events, key=lambda e: (e["days_until"], -("high" in e["impact"])))

    # ── DXY 프록시 (EURUSD 역수) ──

    def _collect_dxy_proxy(self) -> dict:
        """
        DXY 직접 접근 불가 → EURUSD 역수로 대체.
        EUR가 DXY의 ~57% 가중치이므로 합리적 프록시.
        """
        try:
            exchange = self._get_exchange()
            # Binance에 EURUSDT 없으므로 외부 대안: BTC/EUR vs BTC/USDT 비율
            # 더 단순한 접근: BTC 가격의 달러 강세 민감도 추정
            ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1d", limit=30)
            df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
            returns = df["close"].pct_change().dropna().values

            # 달러 강세 프록시: BTC 하락 + 거래량 증가 = 달러 강세 가능성
            recent_7d = returns[-7:]
            btc_7d_change = float((df["close"].iloc[-1] / df["close"].iloc[-8] - 1) * 100)

            # 변동성 프록시: 30일 수익률 표준편차 → 연환산
            vol_30d = float(np.std(returns) * np.sqrt(365) * 100)

            # DXY 트렌드 추정 (BTC 역상관 활용)
            if btc_7d_change < -5:
                dxy_trend = "bullish"  # BTC 하락 → DXY 상승 가능
            elif btc_7d_change > 5:
                dxy_trend = "bearish"  # BTC 상승 → DXY 약세 가능
            else:
                dxy_trend = "neutral"

            return {
                "dxy_trend": dxy_trend,
                "btc_7d_change": round(btc_7d_change, 2),
                "realized_vol_30d": round(vol_30d, 1),
            }
        except Exception:
            return {"dxy_trend": "neutral", "btc_7d_change": 0, "realized_vol_30d": 0}

    # ── VIX 프록시 (실현 변동성) ──

    @staticmethod
    def _classify_vix(realized_vol: float) -> str:
        """
        BTC 30일 실현 변동성 → VIX 레벨 매핑.
        BTC vol은 전통 VIX보다 높으므로 기준 상향 조정.
        """
        if realized_vol < 40:
            return "low"       # 시장 안정
        elif realized_vol < 70:
            return "moderate"  # 정상 범위
        elif realized_vol < 100:
            return "high"      # 주의 필요
        else:
            return "extreme"   # 극도의 변동성

    # ── Risk-on/off 종합 판단 ──

    @staticmethod
    def _assess_risk_appetite(fear_greed_value: float | None,
                               vix_level: str,
                               dxy_trend: str,
                               high_impact_events: int) -> dict:
        """
        복합 지표 기반 Risk-on/off 판단.
        점수 시스템: -10(극 risk-off) ~ +10(극 risk-on)
        """
        score = 0
        reasons = []

        # 1. 공포탐욕 (-3 ~ +3)
        if fear_greed_value is not None:
            if fear_greed_value >= 75:
                score += 3
                reasons.append(f"탐욕 극단({fear_greed_value:.0f})")
            elif fear_greed_value >= 55:
                score += 1
                reasons.append(f"낙관({fear_greed_value:.0f})")
            elif fear_greed_value <= 25:
                score -= 3
                reasons.append(f"공포 극단({fear_greed_value:.0f})")
            elif fear_greed_value <= 40:
                score -= 1
                reasons.append(f"불안({fear_greed_value:.0f})")

        # 2. VIX 레벨 (-3 ~ +2)
        vix_scores = {"low": 2, "moderate": 0, "high": -2, "extreme": -3}
        score += vix_scores.get(vix_level, 0)
        if vix_level in ("high", "extreme"):
            reasons.append(f"고변동성({vix_level})")

        # 3. DXY (-2 ~ +2)
        dxy_scores = {"bearish": 2, "neutral": 0, "bullish": -2}
        score += dxy_scores.get(dxy_trend, 0)
        if dxy_trend == "bullish":
            reasons.append("달러 강세")

        # 4. 고임팩트 이벤트 임박 (-3 ~ 0)
        if high_impact_events >= 2:
            score -= 3
            reasons.append(f"고임팩트 이벤트 {high_impact_events}건 임박")
        elif high_impact_events == 1:
            score -= 1
            reasons.append("고임팩트 이벤트 1건 임박")

        # 결과 분류
        if score >= 3:
            appetite = "risk-on"
            recommendation = "aggressive"
        elif score <= -3:
            appetite = "risk-off"
            recommendation = "defensive"
        else:
            appetite = "neutral"
            recommendation = "neutral"

        # 20x 레버리지 보정: risk-off일 때 더 강하게 반응
        if score <= -5:
            risk_level = "DANGER"
        elif score <= -2 or high_impact_events >= 1:
            risk_level = "CAUTION"
        else:
            risk_level = "SAFE"

        return {
            "risk_appetite": appetite,
            "recommendation": recommendation,
            "risk_level": risk_level,
            "score": score,
            "reasons": reasons,
            "confidence": min(0.4 + abs(score) * 0.05, 0.85),
        }

    def collect_data(self) -> dict:
        """매크로 데이터 수집"""
        data = {}

        # 1. 공포탐욕지수 (CoinGlass)
        if self.cg:
            try:
                fgi = self.cg.get_fear_greed_index()
                data["fear_greed"] = fgi or {}
            except Exception:
                data["fear_greed"] = {}

        # 2. 경제 캘린더
        data["upcoming_events"] = self._get_upcoming_events()

        # 3. DXY 프록시 + VIX (실현 변동성)
        dxy_data = self._collect_dxy_proxy()
        data["dxy"] = dxy_data

        # 4. BTC 일봉 (매크로 반응 분석)
        try:
            exchange = self._get_exchange()
            ohlcv = exchange.fetch_ohlcv("BTC/USDT", "1d", limit=7)
            df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
            data["btc_daily"] = {
                "daily_changes": [
                    round((df["close"].iloc[i] - df["close"].iloc[i - 1]) / df["close"].iloc[i - 1] * 100, 2)
                    for i in range(1, len(df))
                ],
                "weekly_change": round(
                    (df["close"].iloc[-1] - df["close"].iloc[0]) / df["close"].iloc[0] * 100, 2
                ),
                "avg_daily_range": round(
                    ((df["high"] - df["low"]) / df["close"] * 100).mean(), 2
                ),
            }
        except Exception:
            data["btc_daily"] = {}

        return data

    def analyze(self, collected_data: dict, context: dict | None = None) -> AgentMessage:
        """매크로 환경 분석"""
        fear_greed = collected_data.get("fear_greed", {})
        btc_daily = collected_data.get("btc_daily", {})
        events = collected_data.get("upcoming_events", [])
        dxy = collected_data.get("dxy", {})

        # VIX 레벨 판단
        realized_vol = dxy.get("realized_vol_30d", 0)
        vix_level = self._classify_vix(realized_vol)

        # 공포탐욕 값 추출
        fgi_value = None
        if isinstance(fear_greed, dict):
            fgi_value = fear_greed.get("value", fear_greed.get("now", {}).get("value"))
        elif isinstance(fear_greed, (int, float)):
            fgi_value = fear_greed
        if fgi_value is not None:
            fgi_value = float(fgi_value)

        # 고임팩트 이벤트 카운트
        high_impact = sum(1 for e in events if e.get("impact") == "high" and e.get("days_until", 99) <= 1)

        # 이벤트 정보 포맷
        event_text = ""
        if events:
            for e in events[:5]:
                event_text += (f"- {e['event']} (임팩트: {e['impact']}, "
                               f"{e['days_until']}일 후) — {e['note']}\n")
        else:
            event_text = "향후 3일 내 주요 이벤트 없음"

        prompt = f"""다음 데이터를 기반으로 크립토 시장의 매크로 환경을 분석하세요.

## 공포탐욕지수
{f"값: {fgi_value:.0f}" if fgi_value else "데이터 없음"}

## 경제 캘린더 (향후 3일)
{event_text}

## DXY (달러) 동향
- 추정 트렌드: {dxy.get('dxy_trend', 'N/A')}
- BTC 주간 변동: {dxy.get('btc_7d_change', 'N/A')}%

## 변동성 (VIX 프록시)
- BTC 30일 실현 변동성: {realized_vol:.1f}% (연환산)
- VIX 레벨: {vix_level}

## BTC 일봉
- 일별 변동률: {btc_daily.get('daily_changes', 'N/A')}
- 주간 변동: {btc_daily.get('weekly_change', 'N/A')}%
- 평균 일일 레인지: {btc_daily.get('avg_daily_range', 'N/A')}%

20배 레버리지 환경에서 고임팩트 이벤트 전후에는 defensive를 권고하세요."""

        result = self.llm_json(prompt, deep=True)

        if result.get("parse_error"):
            result = self._fallback_analysis(collected_data, fgi_value, vix_level,
                                              dxy.get("dxy_trend", "neutral"), high_impact)

        warnings = []
        if result.get("risk_level") == "DANGER":
            warnings.append("⚠️ 매크로 환경 위험")
        if result.get("recommendation") == "defensive":
            warnings.append("🛡️ 매크로: defensive 모드 권고")
        if vix_level in ("high", "extreme"):
            warnings.append(f"📊 변동성 {vix_level} ({realized_vol:.0f}%)")

        for evt in events:
            if evt.get("impact") == "high" and evt.get("days_until", 99) <= 1:
                warnings.append(f"📅 {evt['event']} — {'오늘' if evt['days_until'] == 0 else '내일'}")

        return self._build_message(
            data=result,
            confidence=result.get("confidence", 0.5),
            reasoning=result.get("reasoning", ""),
            warnings=warnings
        )

    def _fallback_analysis(self, data: dict, fgi_value: float | None,
                            vix_level: str, dxy_trend: str, high_impact: int) -> dict:
        """규칙 기반 폴백 (복합 지표 종합)"""
        assessment = self._assess_risk_appetite(fgi_value, vix_level, dxy_trend, high_impact)
        events = data.get("upcoming_events", [])
        dxy = data.get("dxy", {})

        return {
            "risk_appetite": assessment["risk_appetite"],
            "upcoming_events": events[:5],
            "dxy_trend": dxy_trend,
            "dxy_change_7d": dxy.get("btc_7d_change", 0),
            "vix_level": vix_level,
            "realized_vol_30d": dxy.get("realized_vol_30d", 0),
            "fear_greed": {"value": fgi_value, "label": "unknown"},
            "equity_sentiment": "neutral",
            "recommendation": assessment["recommendation"],
            "risk_level": assessment["risk_level"],
            "confidence": assessment["confidence"],
            "reasoning": (f"규칙 기반: score {assessment['score']:+d}, "
                          f"{', '.join(assessment['reasons'])}")
        }
