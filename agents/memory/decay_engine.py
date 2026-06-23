"""
decay_engine.py — 시간 감쇠 + 성과 기반 가중치 조정
=====================================================
규칙의 시간 감쇠와 성과 기반 가중치를 관리.
qrak 차용: 오래된 규칙은 자동 감쇠, 적중률 낮으면 비활성화.
"""

from __future__ import annotations
import math
import time
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))

# 감쇠 파라미터
DECAY_LAMBDA = 0.01      # 약 70일 반감기
MIN_WEIGHT = 0.1          # 이 이하면 비활성화 대상
MIN_SAMPLE_SIZE = 5       # 성과 평가 최소 샘플 수
EXPECTED_HIT_RATE = 0.5   # 기대 적중률 (이 이하면 패널티)


class DecayEngine:
    """
    규칙의 시간 감쇠 + 성과 기반 가중치 조정.

    감쇠 공식: weight = base_weight × exp(-λ × days_since_creation)
    성과 조정: weight *= (적중률 / 기대 적중률)  [최소 0.5, 최대 1.5]
    비활성화: weight < MIN_WEIGHT
    """

    def __init__(self, rule_store=None, decay_lambda: float = DECAY_LAMBDA,
                 min_weight: float = MIN_WEIGHT):
        self.rule_store = rule_store
        self.decay_lambda = decay_lambda
        self.min_weight = min_weight
        self._last_decay_time: float = 0

    def decay_all(self) -> dict:
        """
        전체 규칙에 시간 감쇠 적용.
        매일 1회 실행 권장.

        Returns:
            {"decayed": int, "deactivated": int, "total": int}
        """
        if not self.rule_store:
            return {"decayed": 0, "deactivated": 0, "total": 0}

        rules = self.rule_store.get_active_rules()
        decayed = 0
        deactivated = 0

        for rule in rules:
            old_weight = rule.weight

            # 시간 감쇠
            new_weight = self._time_decay(rule.created_at, old_weight)

            # 성과 기반 조정
            new_weight = self._performance_adjust(
                new_weight, rule.hits, rule.misses
            )

            # 적용
            if new_weight < self.min_weight:
                self.rule_store.update(rule.rule_id, weight=new_weight, active=False)
                deactivated += 1
            elif abs(new_weight - old_weight) > 0.001:
                self.rule_store.update(rule.rule_id, weight=round(new_weight, 4))
                decayed += 1

        self._last_decay_time = time.time()

        return {
            "decayed": decayed,
            "deactivated": deactivated,
            "total": len(rules),
        }

    def _time_decay(self, created_at: float, current_weight: float) -> float:
        """시간 감쇠 계산"""
        days = (time.time() - created_at) / 86400
        decay_factor = math.exp(-self.decay_lambda * days)
        return current_weight * decay_factor

    def _performance_adjust(self, weight: float,
                            hits: int, misses: int) -> float:
        """성과 기반 가중치 조정"""
        total = hits + misses
        if total < MIN_SAMPLE_SIZE:
            return weight  # 샘플 부족 시 조정하지 않음

        hit_rate = hits / total
        # 적중률 / 기대 적중률 (0.5~1.5 범위)
        perf_mult = max(0.5, min(1.5, hit_rate / EXPECTED_HIT_RATE))
        return weight * perf_mult

    def compute_single_weight(self, created_at: float, hits: int = 0,
                               misses: int = 0) -> float:
        """단일 규칙의 현재 이론 가중치 계산 (적용하지 않음)"""
        w = self._time_decay(created_at, 1.0)
        w = self._performance_adjust(w, hits, misses)
        return round(w, 4)

    def should_run_decay(self, interval_hours: float = 24) -> bool:
        """감쇠 실행이 필요한지 확인"""
        if self._last_decay_time == 0:
            return True
        elapsed = (time.time() - self._last_decay_time) / 3600
        return elapsed >= interval_hours

    def get_decay_preview(self) -> list[dict]:
        """감쇠 적용 시 영향 미리보기"""
        if not self.rule_store:
            return []

        preview = []
        for rule in self.rule_store.get_active_rules():
            new_w = self._time_decay(rule.created_at, rule.weight)
            new_w = self._performance_adjust(new_w, rule.hits, rule.misses)
            if abs(new_w - rule.weight) > 0.001 or new_w < self.min_weight:
                preview.append({
                    "rule_id": rule.rule_id,
                    "condition": rule.condition[:50],
                    "old_weight": round(rule.weight, 4),
                    "new_weight": round(new_w, 4),
                    "will_deactivate": new_w < self.min_weight,
                    "age_days": round(rule.age_days, 1),
                    "hit_rate": round(rule.hit_rate, 3),
                })
        return preview
