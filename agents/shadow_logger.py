"""
shadow_logger.py — Shadow Mode 로깅 + 정확도 측정
===================================================
에이전트 분석 결과를 기록하고, 실제 결과와 비교하여 정확도를 측정.

기록:
  - 에이전트 판결: SAFE/CAUTION/DANGER + 액션 권고
  - 실제 결과: TP/SL/Timeout + 수익률

검증:
  - DANGER → 실제 손실 발생? (True Positive)
  - SAFE → 실제 수익 발생? (True Positive)
  - 에이전트 미구현 기간의 기록 vs 에이전트 적용 기간 비교
"""

from __future__ import annotations
import csv
import os
import json
from datetime import datetime, timezone, timedelta
from typing import Any

from agents.core.protocol import MarketConsensus, RiskLevel

KST = timezone(timedelta(hours=9))


class ShadowLogger:
    """
    Shadow Mode 로거

    CSV 형식으로 분석 결과를 기록하고, 실제 결과를 나중에 추가.
    정확도 측정 리포트 생성.
    """

    COLUMNS = [
        "timestamp",
        "trigger",               # "진입" | "정기분석" | "수동"
        # 컨센서스
        "overall_risk",          # SAFE | CAUTION | DANGER
        "market_regime",
        "btc_bias",
        "consensus_confidence",
        # 에이전트별
        "agent_1_confidence",
        "agent_1_risk",
        "agent_2_confidence",
        "agent_3_confidence",
        "agent_4_confidence",
        "agent_5_confidence",
        # 포지션별 판결
        "position_verdicts_json",  # JSON 문자열
        "warnings_count",
        # 실제 결과 (나중에 채움)
        "actual_outcome",        # TP | SL | TIMEOUT | N/A
        "actual_pnl_pct",
        "outcome_matched",       # True | False | N/A
        "notes",
    ]

    def __init__(self, log_dir: str = ""):
        self.log_dir = log_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "shadow_logs"
        )
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_file = os.path.join(self.log_dir, "shadow_analysis.csv")
        self._ensure_header()

    def _ensure_header(self):
        """CSV 헤더 생성 (파일 없으면)"""
        if not os.path.exists(self.log_file):
            with open(self.log_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.COLUMNS)
                writer.writeheader()

    def log_analysis(self, consensus: MarketConsensus, trigger: str = "정기분석"):
        """분석 결과 기록"""
        # 에이전트별 신뢰도/리스크 추출
        agent_data = {}
        for i, msg in enumerate(consensus.agent_messages, 1):
            agent_data[f"agent_{i}_confidence"] = round(msg.confidence, 3)
            if i == 1:
                agent_data["agent_1_risk"] = msg.data.get("risk_level", "N/A")

        # 포지션 판결 JSON
        verdicts_json = json.dumps(consensus.position_verdicts, ensure_ascii=False) \
            if consensus.position_verdicts else "[]"

        row = {
            "timestamp": consensus.timestamp,
            "trigger": trigger,
            "overall_risk": consensus.overall_risk.value,
            "market_regime": consensus.market_regime.value,
            "btc_bias": consensus.btc_bias,
            "consensus_confidence": round(consensus.confidence, 3),
            "agent_1_confidence": agent_data.get("agent_1_confidence", ""),
            "agent_1_risk": agent_data.get("agent_1_risk", ""),
            "agent_2_confidence": agent_data.get("agent_2_confidence", ""),
            "agent_3_confidence": agent_data.get("agent_3_confidence", ""),
            "agent_4_confidence": agent_data.get("agent_4_confidence", ""),
            "agent_5_confidence": agent_data.get("agent_5_confidence", ""),
            "position_verdicts_json": verdicts_json[:1000],
            "warnings_count": len(consensus.warnings),
            "actual_outcome": "",
            "actual_pnl_pct": "",
            "outcome_matched": "",
            "notes": "",
        }

        with open(self.log_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.COLUMNS)
            writer.writerow(row)

    def update_outcome(self, timestamp: str, outcome: str, pnl_pct: float):
        """실제 결과 업데이트 (나중에 호출)"""
        if not os.path.exists(self.log_file):
            return

        rows = []
        with open(self.log_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["timestamp"] == timestamp and not row.get("actual_outcome"):
                    row["actual_outcome"] = outcome
                    row["actual_pnl_pct"] = str(round(pnl_pct, 2))
                    # 정확도 판단
                    risk = row.get("overall_risk", "")
                    if risk == "DANGER" and pnl_pct < 0:
                        row["outcome_matched"] = "True"  # 위험 예측 → 실제 손실
                    elif risk == "SAFE" and pnl_pct > 0:
                        row["outcome_matched"] = "True"  # 안전 예측 → 실제 수익
                    elif risk == "DANGER" and pnl_pct > 0:
                        row["outcome_matched"] = "False"  # 위험 예측 → 실제 수익 (False Positive)
                    elif risk == "SAFE" and pnl_pct < 0:
                        row["outcome_matched"] = "False"  # 안전 예측 → 실제 손실 (False Negative)
                    else:
                        row["outcome_matched"] = "Partial"
                rows.append(row)

        with open(self.log_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def generate_accuracy_report(self) -> dict:
        """정확도 리포트 생성"""
        if not os.path.exists(self.log_file):
            return {"error": "로그 파일 없음"}

        total = 0
        matched = 0
        false_positive = 0  # DANGER 예측 → 실제 수익
        false_negative = 0  # SAFE 예측 → 실제 손실
        risk_counts = {"SAFE": 0, "CAUTION": 0, "DANGER": 0}
        outcomes = {"TP": 0, "SL": 0, "TIMEOUT": 0}

        with open(self.log_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                risk = row.get("overall_risk", "")
                if risk in risk_counts:
                    risk_counts[risk] += 1

                outcome = row.get("actual_outcome", "")
                if outcome in outcomes:
                    outcomes[outcome] += 1

                match = row.get("outcome_matched", "")
                if match in ("True", "False"):
                    total += 1
                    if match == "True":
                        matched += 1
                    elif row.get("overall_risk") == "DANGER":
                        false_positive += 1
                    elif row.get("overall_risk") == "SAFE":
                        false_negative += 1

        accuracy = matched / max(total, 1) * 100
        fp_rate = false_positive / max(total, 1) * 100
        fn_rate = false_negative / max(total, 1) * 100

        return {
            "total_analyses": risk_counts["SAFE"] + risk_counts["CAUTION"] + risk_counts["DANGER"],
            "evaluated": total,
            "accuracy_pct": round(accuracy, 1),
            "false_positive_rate": round(fp_rate, 1),  # 과도한 경고
            "false_negative_rate": round(fn_rate, 1),  # 위험 미감지
            "risk_distribution": risk_counts,
            "outcome_distribution": outcomes,
        }
