"""
memory/store.py — 계층적 메모리 시스템 (FinMem 차용)
====================================================
단기: 최근 분석 결과 (수 시간) — deque
중기: 일별 패턴 집계 (수 일) — 24시간 단위 요약
장기: 시장 사이클 학습 (수 주~수 개월) — Phase 3 본격 구현 예정
"""

from __future__ import annotations
import json
import os
from datetime import datetime, timezone, timedelta
from collections import deque, defaultdict
from typing import Any

from agents.core.protocol import MarketConsensus, RiskLevel

KST = timezone(timedelta(hours=9))


class AnalysisMemory:
    """
    분석 결과 메모리 스토어 (FinMem 차용 계층 구조)

    - 단기: 최근 N개 분석 결과 (deque, 수 시간)
    - 중기: 일별 집계 패턴 (최근 30일)
    - 장기: Phase 3에서 구현 예정 (사이클 학습)
    """

    def __init__(self, max_short_term: int = 50, max_mid_term_days: int = 30,
                 save_path: str = ""):
        # 단기 메모리
        self._short_term: deque[dict] = deque(maxlen=max_short_term)
        # 중기 메모리: {날짜 문자열: DailySummary}
        self._mid_term: dict[str, dict] = {}
        self._max_mid_term_days = max_mid_term_days
        self.save_path = save_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "memory"
        )

    def store(self, consensus: MarketConsensus):
        """분석 결과 저장 (단기 + 중기 동시 업데이트)"""
        record = {
            "timestamp": consensus.timestamp,
            "overall_risk": consensus.overall_risk.value,
            "market_regime": consensus.market_regime.value,
            "btc_bias": consensus.btc_bias,
            "confidence": consensus.confidence,
            "warnings_count": len(consensus.warnings),
            "agents_count": len(consensus.agent_messages),
            "verdicts_count": len(consensus.position_verdicts),
        }
        self._short_term.append(record)

        # 중기 메모리 업데이트
        self._update_mid_term(record)

    # ── 단기 메모리 ──

    def get_recent(self, n: int = 5) -> list[dict]:
        """최근 N개 분석 결과 조회"""
        return list(self._short_term)[-n:]

    def get_risk_trend(self, n: int = 10) -> str:
        """최근 N개 분석의 리스크 추세"""
        recent = self.get_recent(n)
        if not recent:
            return "NO_DATA"

        risk_scores = {
            "SAFE": 0, "CAUTION": 1, "DANGER": 2
        }
        scores = [risk_scores.get(r["overall_risk"], 1) for r in recent]

        if len(scores) < 2:
            return "INSUFFICIENT"

        avg_first_half = sum(scores[:len(scores)//2]) / max(len(scores)//2, 1)
        avg_second_half = sum(scores[len(scores)//2:]) / max(len(scores) - len(scores)//2, 1)

        if avg_second_half > avg_first_half + 0.3:
            return "DETERIORATING"
        elif avg_second_half < avg_first_half - 0.3:
            return "IMPROVING"
        else:
            return "STABLE"

    # ── 중기 메모리 (일별 패턴 집계) ──

    def _update_mid_term(self, record: dict):
        """단기 레코드를 일별 집계에 반영"""
        try:
            ts = record.get("timestamp", "")
            if "T" in ts:
                date_key = ts.split("T")[0]
            else:
                date_key = datetime.now(KST).strftime("%Y-%m-%d")
        except Exception:
            date_key = datetime.now(KST).strftime("%Y-%m-%d")

        if date_key not in self._mid_term:
            self._mid_term[date_key] = {
                "date": date_key,
                "analysis_count": 0,
                "risk_counts": {"SAFE": 0, "CAUTION": 0, "DANGER": 0},
                "regime_counts": {"risk-on": 0, "neutral": 0, "risk-off": 0},
                "bias_counts": {"bullish": 0, "neutral": 0, "bearish": 0},
                "avg_confidence": 0.0,
                "total_warnings": 0,
                "_conf_sum": 0.0,
            }

        day = self._mid_term[date_key]
        day["analysis_count"] += 1
        day["risk_counts"][record.get("overall_risk", "CAUTION")] = \
            day["risk_counts"].get(record.get("overall_risk", "CAUTION"), 0) + 1
        day["regime_counts"][record.get("market_regime", "neutral")] = \
            day["regime_counts"].get(record.get("market_regime", "neutral"), 0) + 1
        day["bias_counts"][record.get("btc_bias", "neutral")] = \
            day["bias_counts"].get(record.get("btc_bias", "neutral"), 0) + 1
        day["total_warnings"] += record.get("warnings_count", 0)
        day["_conf_sum"] += record.get("confidence", 0.5)
        day["avg_confidence"] = round(day["_conf_sum"] / day["analysis_count"], 3)

        # 오래된 데이터 정리
        self._prune_mid_term()

    def _prune_mid_term(self):
        """max_mid_term_days 초과 데이터 정리"""
        if len(self._mid_term) <= self._max_mid_term_days:
            return
        sorted_dates = sorted(self._mid_term.keys())
        while len(self._mid_term) > self._max_mid_term_days:
            del self._mid_term[sorted_dates.pop(0)]

    def get_daily_summary(self, date: str = "") -> dict | None:
        """특정 날짜의 일별 집계 조회"""
        if not date:
            date = datetime.now(KST).strftime("%Y-%m-%d")
        day = self._mid_term.get(date)
        if day is None:
            return None
        # _conf_sum은 내부용이므로 제외
        return {k: v for k, v in day.items() if not k.startswith("_")}

    def get_multi_day_pattern(self, n_days: int = 7) -> dict:
        """
        최근 N일간 패턴 분석.

        반환:
        - dominant_risk: 가장 빈번한 리스크 레벨
        - dominant_regime: 가장 빈번한 regime
        - risk_shift: 리스크 변화 추세 (escalating / de-escalating / stable)
        - confidence_trend: 평균 confidence 추세 (rising / falling / stable)
        - danger_days: DANGER가 최다인 날의 수
        - pattern_summary: 한줄 요약
        """
        sorted_dates = sorted(self._mid_term.keys())[-n_days:]
        if not sorted_dates:
            return {"pattern_summary": "데이터 부족", "days_available": 0}

        days = [self._mid_term[d] for d in sorted_dates]

        # 전체 기간 리스크 집계
        total_risk = {"SAFE": 0, "CAUTION": 0, "DANGER": 0}
        total_regime = {"risk-on": 0, "neutral": 0, "risk-off": 0}
        confidences = []

        for day in days:
            for r, cnt in day.get("risk_counts", {}).items():
                total_risk[r] = total_risk.get(r, 0) + cnt
            for r, cnt in day.get("regime_counts", {}).items():
                total_regime[r] = total_regime.get(r, 0) + cnt
            confidences.append(day.get("avg_confidence", 0.5))

        dominant_risk = max(total_risk, key=total_risk.get)
        dominant_regime = max(total_regime, key=total_regime.get)

        # 리스크 변화 추세 (전반 vs 후반)
        risk_scores = {"SAFE": 0, "CAUTION": 1, "DANGER": 2}
        day_risk_avgs = []
        for day in days:
            rc = day.get("risk_counts", {})
            total = sum(rc.values()) or 1
            avg = sum(risk_scores.get(r, 1) * cnt for r, cnt in rc.items()) / total
            day_risk_avgs.append(avg)

        if len(day_risk_avgs) >= 2:
            half = len(day_risk_avgs) // 2
            first_avg = sum(day_risk_avgs[:half]) / max(half, 1)
            second_avg = sum(day_risk_avgs[half:]) / max(len(day_risk_avgs) - half, 1)
            if second_avg > first_avg + 0.3:
                risk_shift = "escalating"
            elif second_avg < first_avg - 0.3:
                risk_shift = "de-escalating"
            else:
                risk_shift = "stable"
        else:
            risk_shift = "insufficient"

        # Confidence 추세
        if len(confidences) >= 2:
            half = len(confidences) // 2
            first_conf = sum(confidences[:half]) / max(half, 1)
            second_conf = sum(confidences[half:]) / max(len(confidences) - half, 1)
            if second_conf > first_conf + 0.05:
                conf_trend = "rising"
            elif second_conf < first_conf - 0.05:
                conf_trend = "falling"
            else:
                conf_trend = "stable"
        else:
            conf_trend = "insufficient"

        danger_days = sum(
            1 for d in days
            if d.get("risk_counts", {}).get("DANGER", 0) ==
               max(d.get("risk_counts", {}).values() or [0])
            and d.get("risk_counts", {}).get("DANGER", 0) > 0
        )

        # 한줄 요약
        summary_parts = [f"{len(sorted_dates)}일간"]
        summary_parts.append(f"주요리스크={dominant_risk}")
        if risk_shift != "stable":
            summary_parts.append(f"추세={risk_shift}")
        if danger_days > 0:
            summary_parts.append(f"DANGER {danger_days}일")

        return {
            "days_available": len(sorted_dates),
            "dominant_risk": dominant_risk,
            "dominant_regime": dominant_regime,
            "risk_shift": risk_shift,
            "confidence_trend": conf_trend,
            "danger_days": danger_days,
            "avg_confidence": round(sum(confidences) / len(confidences), 3),
            "daily_details": [
                {k: v for k, v in d.items() if not k.startswith("_")}
                for d in days
            ],
            "pattern_summary": " | ".join(summary_parts),
        }
