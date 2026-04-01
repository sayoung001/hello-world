"""
position_judge.py — Agent 5: 포지션 심판
=========================================
현재 보유 포지션 각각에 대한 심층 분석 및 판결.
Market Context Layer의 컨센서스 + 개별 코인 데이터를 종합.

기존 position_doctor.py의 진단 로직을 LLM 기반으로 확장.
"""

from __future__ import annotations
import json
from typing import Any

from agents.core.base import AgentBase
from agents.core.protocol import (
    AgentMessage, PositionInfo, PositionVerdict,
    RiskLevel, ActionType,
)


class PositionJudgeAgent(AgentBase):
    """
    포지션 심판 에이전트

    입력:
    - Agent 1~4의 분석 결과 (MarketConsensus)
    - 현재 오픈 포지션 목록
    - 20x 레버리지 사실

    출력:
    - 각 포지션별 PositionVerdict (홀딩/축소/청산 판결)
    """

    def __init__(self):
        super().__init__(
            agent_id="agent_5_judge",
            agent_name="포지션 심판",
            role_description="현재 보유 포지션 각각에 대한 리스크 평가 및 판결"
        )
        self._analysis_type = "subjective"

    def _get_system_prompt(self) -> str:
        return """당신은 20배 레버리지 크립토 선물 포지션의 심판관입니다.

Market Context Layer(BTC 구조, 매크로, 상관관계, 알트 생태계)의 분석 결과와
개별 포지션 데이터를 종합하여 각 포지션에 대한 최종 판결을 내립니다.

핵심 원칙:
1. 20배 레버리지 = ~4% 역행 시 청산. 생존이 최우선.
2. 청산 거리가 2% 이내면 무조건 DANGER
3. 시장 환경(consensus)과 개별 포지션 상태를 모두 고려
4. 불확실하면 보수적으로 (축소 > 홀딩)

반드시 JSON 형식으로 출력:
```json
{
  "verdicts": [
    {
      "symbol": "XXXUSDT",
      "direction": "long|short",
      "risk_level": "SAFE|CAUTION|DANGER",
      "action": "hold|reduce_size|close|adjust_sl|adjust_tp",
      "confidence": 0.0~1.0,
      "breakout_probability": 0.0~1.0,
      "hold_duration": "N시간 or 재평가 필요",
      "reasoning": "판결 근거",
      "dissent": "반대 의견 (있으면)"
    }
  ],
  "portfolio_risk": "SAFE|CAUTION|DANGER",
  "overall_reasoning": "포트폴리오 전체 판단 근거"
}
```"""

    def collect_data(self) -> dict:
        """
        데이터 수집은 context로 전달받으므로 빈 dict 반환.
        포지션 데이터는 Orchestrator가 run() 시 context로 주입.
        """
        return {}

    def analyze(self, collected_data: dict, context: dict | None = None) -> AgentMessage:
        """포지션별 심판 수행"""
        if not context:
            return self._build_message(
                data={"verdicts": [], "error": "컨텍스트 없음"},
                confidence=0.0,
                warnings=["포지션/컨센서스 데이터 미제공"]
            )

        consensus = context.get("consensus", {})
        positions = context.get("positions", [])
        agent_analyses = context.get("agent_analyses", [])

        if not positions:
            return self._build_message(
                data={"verdicts": [], "message": "오픈 포지션 없음"},
                confidence=1.0,
            )

        # LLM에 전달할 프롬프트 구성
        prompt = self._build_judge_prompt(consensus, positions, agent_analyses)
        result = self.llm_json(prompt, deep=True, max_tokens=3000)

        if result.get("parse_error"):
            result = self._fallback_judge(consensus, positions)

        # 경고 생성
        warnings = []
        for v in result.get("verdicts", []):
            if v.get("risk_level") == "DANGER":
                warnings.append(f"🔴 {v['symbol']} DANGER — {v.get('reasoning', '')[:50]}")
            elif v.get("action") == "close":
                warnings.append(f"⚠️ {v['symbol']} 즉시 청산 권고")

        confidence = 0.7  # 기본 신뢰도
        if result.get("portfolio_risk") == "DANGER":
            confidence = 0.85  # 위험 판단은 더 확신 있게

        return self._build_message(
            data=result,
            confidence=confidence,
            reasoning=result.get("overall_reasoning", ""),
            warnings=warnings
        )

    def _build_judge_prompt(self, consensus: dict, positions: list,
                            agent_analyses: list) -> str:
        """심판 프롬프트 구성"""
        pos_text = "\n".join([
            f"  - {p['symbol']} | {p['direction']} | 진입: {p['entry_price']} | "
            f"현재: {p['current_price']} | PnL: {p['pnl_pct']:.2f}% | "
            f"ROE: {p['roe_pct']:.1f}% | 청산거리: {p['liquidation_distance_pct']:.2f}% | "
            f"리스크: {p['risk_level']} | 보유: {p['hold_candles']}캔들"
            for p in positions
        ])

        analysis_text = "\n".join([
            f"  [{a['agent']}] (confidence: {a['confidence']:.2f}): "
            f"{json.dumps(a['data'], ensure_ascii=False)[:200]}"
            for a in agent_analyses
        ])

        return f"""## 시장 컨센서스
- 종합 리스크: {consensus.get('overall_risk', 'N/A')}
- 시장 상태: {consensus.get('market_regime', 'N/A')}
- BTC 편향: {consensus.get('btc_bias', 'N/A')}
- 경고: {', '.join(consensus.get('warnings', [])[:5])}

## 에이전트 분석 요약
{analysis_text}

## 현재 오픈 포지션
{pos_text}

위 정보를 종합하여 각 포지션에 대한 최종 판결을 내려주세요.
20배 레버리지 생존 원칙을 반드시 적용하세요."""

    def _fallback_judge(self, consensus: dict, positions: list) -> dict:
        """규칙 기반 폴백 판결"""
        verdicts = []
        for p in positions:
            liq_dist = p.get("liquidation_distance_pct", 5.0)
            mkt_risk = consensus.get("overall_risk", "CAUTION")

            if liq_dist < 1.5:
                risk, action = "DANGER", "close"
                reasoning = f"청산 거리 {liq_dist:.1f}% — 즉시 청산 필요"
            elif liq_dist < 3.0 or mkt_risk == "DANGER":
                risk, action = "CAUTION", "reduce_size"
                reasoning = f"청산 거리 {liq_dist:.1f}%, 시장 리스크 {mkt_risk}"
            else:
                risk, action = "SAFE", "hold"
                reasoning = f"청산 거리 {liq_dist:.1f}% — 여유 있음"

            verdicts.append({
                "symbol": p["symbol"],
                "direction": p["direction"],
                "risk_level": risk,
                "action": action,
                "confidence": 0.5,
                "hold_duration": "재평가 필요",
                "reasoning": reasoning,
            })

        return {
            "verdicts": verdicts,
            "portfolio_risk": consensus.get("overall_risk", "CAUTION"),
            "overall_reasoning": "규칙 기반 폴백 판결 (LLM 파싱 실패)"
        }
