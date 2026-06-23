"""
vision_scorer.py — Vision AI 분석 결과 → 수치 피처 변환
=========================================================
패턴 분석 결과를 RL state에 주입 가능한 수치 피처로 변환.
시그널 방향과 Vision 예측의 일치도 계산.
"""

from __future__ import annotations


class VisionScorer:
    """
    PatternAnalyzer의 분석 결과 → RL/리스크 에이전트용 수치 피처 변환.

    출력 피처:
    - vision_trend: -1.0 ~ 1.0 (추세 강도)
    - vision_pattern_conf: 0.0 ~ 1.0 (패턴 인식 신뢰도)
    - vision_breakout_align: -1.0 ~ 1.0 (시그널 방향 일치도)
    - vision_sl_suggest: 차트 기반 SL 제안가
    - vision_risk_level: 0.0 ~ 1.0 (리스크 수준)
    """

    RISK_MAP = {"low": 0.2, "medium": 0.5, "high": 0.8}

    def to_state_features(self, analysis: dict,
                          signal_direction: str = "") -> dict:
        """
        Vision AI 분석 결과 → RL state 피처.

        Args:
            analysis: PatternAnalyzer.analyze() 결과
            signal_direction: 시그널 방향 ("long" / "short")

        Returns:
            수치 피처 dict
        """
        if not analysis or analysis.get("parse_error") or analysis.get("error"):
            return self._empty_features()

        # 추세 강도
        trend = float(analysis.get("trend_strength", 0.0))
        trend = max(-1.0, min(1.0, trend))

        # 패턴 인식 신뢰도 (최고 신뢰도 패턴 기준)
        patterns = analysis.get("patterns", [])
        if patterns:
            max_conf = max(p.get("confidence", 0) for p in patterns)
            pattern_conf = max_conf / 100.0
        else:
            pattern_conf = 0.0

        # 시그널 방향과 Vision 예측 일치도
        prediction = analysis.get("breakout_prediction", "UNCERTAIN").upper()
        if signal_direction and prediction != "UNCERTAIN":
            if signal_direction.lower() == "long" and prediction == "LONG":
                breakout_align = 1.0
            elif signal_direction.lower() == "short" and prediction == "SHORT":
                breakout_align = 1.0
            elif signal_direction.lower() == "long" and prediction == "SHORT":
                breakout_align = -1.0
            elif signal_direction.lower() == "short" and prediction == "LONG":
                breakout_align = -1.0
            else:
                breakout_align = 0.0
        else:
            breakout_align = 0.0

        # SL 제안
        sl_suggest = float(analysis.get("chart_based_sl", 0.0))

        # 리스크 레벨
        risk_str = analysis.get("risk_assessment", "medium")
        risk_level = self.RISK_MAP.get(risk_str, 0.5)

        return {
            "vision_trend": round(trend, 4),
            "vision_pattern_conf": round(pattern_conf, 4),
            "vision_breakout_align": round(breakout_align, 4),
            "vision_sl_suggest": round(sl_suggest, 2),
            "vision_risk_level": round(risk_level, 4),
        }

    def compute_confidence_adjustment(self, features: dict) -> float:
        """
        Vision 피처 기반 confidence 조정값 계산.

        Returns:
            -0.15 ~ +0.10 범위의 조정값
            - Vision이 시그널과 일치 → +0.05~0.10
            - Vision이 시그널과 반대 → -0.10~-0.15
            - 불확실 → 0.0
        """
        align = features.get("vision_breakout_align", 0)
        conf = features.get("vision_pattern_conf", 0)

        if conf < 0.3:
            return 0.0  # 패턴 인식 신뢰도 낮으면 조정 없음

        if align > 0:
            return round(min(0.10, align * conf * 0.15), 4)
        elif align < 0:
            return round(max(-0.15, align * conf * 0.20), 4)
        return 0.0

    def _empty_features(self) -> dict:
        """에러/빈 분석 시 기본 피처"""
        return {
            "vision_trend": 0.0,
            "vision_pattern_conf": 0.0,
            "vision_breakout_align": 0.0,
            "vision_sl_suggest": 0.0,
            "vision_risk_level": 0.5,
        }
