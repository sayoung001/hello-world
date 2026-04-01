"""
memory/store.py — 계층적 메모리 시스템 (FinMem 차용)
====================================================
단기: 최근 분석 결과 (수 시간)
중기: 패턴 인식 (수 일)
장기: 시장 사이클 학습 (수 주~수 개월)

Phase 3에서 본격 구현 예정. 현재는 단기 메모리(최근 분석 기록)만 지원.
"""

from __future__ import annotations
import json
import os
from datetime import datetime, timezone, timedelta
from collections import deque
from typing import Any

from agents.core.protocol import MarketConsensus, RiskLevel

KST = timezone(timedelta(hours=9))


class AnalysisMemory:
    """
    분석 결과 메모리 스토어

    - 단기: 최근 N개 분석 결과 (deque)
    - 중기/장기: Phase 3에서 구현 예정
    """

    def __init__(self, max_short_term: int = 50, save_path: str = ""):
        self._short_term: deque[dict] = deque(maxlen=max_short_term)
        self.save_path = save_path or os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "memory"
        )

    def store(self, consensus: MarketConsensus):
        """분석 결과 저장"""
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
