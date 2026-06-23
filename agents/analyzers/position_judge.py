"""
position_judge.py — Agent 5: 포지션 심판
=========================================
현재 보유 포지션 각각에 대한 심층 분석 및 판결.
Market Context Layer의 컨센서스 + 개별 코인 데이터를 종합.

핵심 기능:
- 종합 판결 로직 (다중 지표 스코어링)
- 홀딩 시간 추천 알고리즘
- SL/TP 조정 권고
"""

from __future__ import annotations
import json
from typing import Any

from agents.core.base import AgentBase
from agents.core.protocol import (
    AgentMessage, PositionInfo, PositionVerdict,
    RiskLevel, ActionType,
)

# 20x 레버리지 기준 리스크 임계값
LIQ_DANGER_PCT = 1.5      # 청산 거리 1.5% 이내 = 즉시 청산
LIQ_CAUTION_PCT = 3.0     # 청산 거리 3% 이내 = 축소 권고
LIQ_SAFE_PCT = 4.5        # 청산 거리 4.5% 이상 = 안전

# 홀딩 시간 기준 (15분 캔들 기준)
HOLD_SHORT = 8             # 2시간: 단기 스캘핑
HOLD_MEDIUM = 24           # 6시간: 표준 홀딩
HOLD_LONG = 48             # 12시간: 장기 홀딩
HOLD_TIMEOUT = 192         # 48시간: 타임아웃 임박


class PositionJudgeAgent(AgentBase):
    """
    포지션 심판 에이전트

    판결 프로세스:
    1. 각 포지션별 위험 점수 계산 (0~100)
    2. 시장 컨텍스트와 포지션 방향 정합성 확인
    3. 홀딩 시간 기반 최적 행동 추천
    4. SL/TP 조정 필요성 판단
    5. 종합 판결 (hold/reduce/close/adjust)
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
2. 청산 거리가 1.5% 이내면 무조건 DANGER
3. 시장 환경(consensus)과 개별 포지션 상태를 모두 고려
4. 불확실하면 보수적으로 (축소 > 홀딩)
5. 홀딩 시간이 길수록 리스크 증가 (시간 감쇠)

반드시 JSON 형식으로 출력:
```json
{
  "verdicts": [
    {
      "symbol": "XXXUSDT",
      "direction": "long|short",
      "risk_level": "SAFE|CAUTION|DANGER",
      "risk_score": 0~100,
      "action": "hold|reduce_size|close|adjust_sl|adjust_tp",
      "confidence": 0.0~1.0,
      "hold_recommendation": {
        "optimal_hold_h": N시간,
        "remaining_h": N시간,
        "urgency": "low|medium|high"
      },
      "sl_tp_adjustment": {
        "sl_action": "keep|tighten|widen|move_to_breakeven",
        "tp_action": "keep|extend|reduce",
        "reasoning": "조정 근거"
      },
      "reasoning": "판결 근거",
      "dissent": "반대 의견 (있으면)"
    }
  ],
  "portfolio_risk": "SAFE|CAUTION|DANGER",
  "portfolio_risk_score": 0~100,
  "overall_reasoning": "포트폴리오 전체 판단 근거"
}
```"""

    def collect_data(self) -> dict:
        """데이터 수집은 context로 전달받음"""
        return {}

    def analyze(self, collected_data: dict, context: dict | None = None,
                crash_context: dict | None = None) -> AgentMessage:
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
            result = self._fallback_judge(consensus, positions, agent_analyses)

        # 경고 생성
        warnings = []
        for v in result.get("verdicts", []):
            if v.get("risk_level") == "DANGER":
                warnings.append(f"🔴 {v['symbol']} DANGER — {v.get('reasoning', '')[:50]}")
            elif v.get("action") == "close":
                warnings.append(f"⚠️ {v['symbol']} 즉시 청산 권고")
            elif v.get("action") == "adjust_sl":
                sl_adj = v.get("sl_tp_adjustment", {})
                warnings.append(f"🔧 {v['symbol']} SL 조정 권고: {sl_adj.get('sl_action', '')}")

        confidence = 0.7
        if result.get("portfolio_risk") == "DANGER":
            confidence = 0.75  # DANGER 판정 confidence 과도 상향 방지 (0.85 → 0.75)

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
            f"리스크: {p['risk_level']} | 보유: {p['hold_candles']}캔들 ({p['hold_candles']*15/60:.1f}시간)"
            for p in positions
        ])

        analysis_text = "\n".join([
            f"  [{a['agent']}] (confidence: {a['confidence']:.2f}): "
            f"{json.dumps(a['data'], ensure_ascii=False)[:300]}"
            for a in agent_analyses
        ])

        return f"""## 시장 컨센서스
- 종합 리스크: {consensus.get('overall_risk', 'N/A')}
- 시장 상태: {consensus.get('market_regime', 'N/A')}
- BTC 편향: {consensus.get('btc_bias', 'N/A')}
- 신뢰도: {consensus.get('confidence', 'N/A')}
- 경고: {', '.join(consensus.get('warnings', [])[:5])}

## 에이전트 분석 요약
{analysis_text}

## 현재 오픈 포지션
{pos_text}

## 판결 요청
각 포지션에 대해:
1. 리스크 점수 (0~100, 100=최대위험) 산정
2. 시장 방향과 포지션 방향 정합성 확인
3. 최적 홀딩 시간과 잔여 시간 추천
4. SL/TP 조정 필요성 판단
5. 최종 판결 (hold/reduce/close/adjust)

20배 레버리지 생존 원칙을 반드시 적용하세요."""

    # ── 규칙 기반 종합 판결 (폴백) ──

    def _fallback_judge(self, consensus: dict, positions: list,
                        agent_analyses: list = None) -> dict:
        """규칙 기반 폴백: 다중 지표 스코어링"""
        mkt_risk = consensus.get("overall_risk", "CAUTION")
        btc_bias = consensus.get("btc_bias", "neutral")

        # 에이전트 분석에서 유용한 데이터 추출
        btc_data = {}
        alt_betas = {}
        for a in (agent_analyses or []):
            data = a.get("data", {})
            if "BTC" in a.get("agent", ""):
                btc_data = data
            if "알트" in a.get("agent", "") or "alt" in a.get("agent", "").lower():
                alt_betas = data.get("coin_betas", {})

        verdicts = []
        total_risk = 0

        for p in positions:
            score = self._compute_risk_score(p, mkt_risk, btc_bias, btc_data, alt_betas)
            hold_rec = self._recommend_hold_time(p, mkt_risk, score)
            sl_tp = self._recommend_sl_tp(p, mkt_risk, btc_data)

            # 액션 결정
            if score >= 80:
                risk, action = "DANGER", "close"
            elif score >= 60:
                risk, action = "CAUTION", "reduce_size"
            elif sl_tp.get("sl_action") not in ("keep", None):
                risk, action = "CAUTION", "adjust_sl"
            else:
                risk, action = "SAFE", "hold"

            # 신뢰도: 데이터 충실도 기반 (PNL, 청산거리, 홀딩시간이 유효할수록 높음)
            data_quality = 0.4  # 기본 (규칙 기반)
            if p.get("pnl_pct", 0) != 0:
                data_quality += 0.15  # 실시간 PNL 있음
            if p.get("liquidation_distance_pct", 5.0) != 5.0:
                data_quality += 0.15  # 청산거리 유효
            if p.get("hold_candles", 0) > 0:
                data_quality += 0.1   # 홀딩시간 유효
            # 극단 스코어일수록 판단 확신 높음
            extremity = abs(score - 50) / 50  # 0~1 (50에서 멀수록 확신)
            conf = min(data_quality + extremity * 0.2, 0.95)

            verdicts.append({
                "symbol": p["symbol"],
                "direction": p["direction"],
                "risk_level": risk,
                "risk_score": score,
                "action": action,
                "confidence": round(conf, 2),
                "hold_recommendation": hold_rec,
                "sl_tp_adjustment": sl_tp,
                "reasoning": self._format_reasoning(p, score, mkt_risk, btc_bias),
                "dissent": self._generate_dissent(p, score, btc_bias),
            })
            total_risk += score

        portfolio_score = int(total_risk / max(len(positions), 1))
        if portfolio_score >= 70:
            portfolio_risk = "DANGER"
        elif portfolio_score >= 40:
            portfolio_risk = "CAUTION"
        else:
            portfolio_risk = "SAFE"

        return {
            "verdicts": verdicts,
            "portfolio_risk": portfolio_risk,
            "portfolio_risk_score": portfolio_score,
            "overall_reasoning": f"규칙 기반 판결: 평균 리스크 {portfolio_score}/100, 시장 {mkt_risk}"
        }

    # ── 리스크 점수 계산 (0~100) ──

    @staticmethod
    def _compute_risk_score(pos: dict, mkt_risk: str, btc_bias: str,
                             btc_data: dict, alt_betas: dict) -> int:
        """
        다중 지표 기반 리스크 점수 (0=안전, 100=최대위험).

        가중치:
        - 청산 거리: 40% (생존 최우선)
        - 방향 정합성: 20%
        - 시장 환경: 15%
        - 홀딩 시간: 15%
        - 코인 베타: 10%
        """
        score = 0
        liq_dist = pos.get("liquidation_distance_pct", 5.0)
        direction = pos.get("direction", "long")
        hold_candles = pos.get("hold_candles", 0)
        pnl_pct = pos.get("pnl_pct", 0)

        # 1. 청산 거리 (40점)
        if liq_dist < LIQ_DANGER_PCT:
            score += 40
        elif liq_dist < LIQ_CAUTION_PCT:
            score += int(30 * (LIQ_CAUTION_PCT - liq_dist) / (LIQ_CAUTION_PCT - LIQ_DANGER_PCT))
        elif liq_dist < LIQ_SAFE_PCT:
            score += int(10 * (LIQ_SAFE_PCT - liq_dist) / (LIQ_SAFE_PCT - LIQ_CAUTION_PCT))

        # 2. 방향 정합성 (20점)
        direction_aligned = (
            (direction == "long" and btc_bias == "bullish") or
            (direction == "short" and btc_bias == "bearish") or
            btc_bias == "neutral"
        )
        if not direction_aligned:
            score += 20  # BTC 방향과 반대
        elif btc_bias == "neutral":
            score += 5   # 방향 불명확

        # 3. 시장 환경 (15점)
        mkt_scores = {"DANGER": 15, "CAUTION": 8, "SAFE": 0}
        score += mkt_scores.get(mkt_risk, 8)

        # 4. 홀딩 시간 감쇠 (15점)
        if hold_candles > HOLD_TIMEOUT:
            score += 15
        elif hold_candles > HOLD_LONG:
            score += 10
        elif hold_candles > HOLD_MEDIUM:
            score += 5
        # 단기 + 손실 중이면 더 위험
        if hold_candles > HOLD_SHORT and pnl_pct < -1:
            score += 5

        # 5. 코인 베타 (10점)
        symbol_clean = pos.get("symbol", "").replace("/USDT", "").replace("USDT", "")
        beta = alt_betas.get(symbol_clean, 1.0)
        if abs(beta) > 2.0:
            score += 10  # 과도한 변동성
        elif abs(beta) > 1.5:
            score += 5

        return min(score, 100)

    # ── 홀딩 시간 추천 ──

    @staticmethod
    def _recommend_hold_time(pos: dict, mkt_risk: str, risk_score: int) -> dict:
        """
        최적 홀딩 시간 추천.
        기반: 리스크 점수 + 시장 환경 + 현재 수익률
        """
        hold_candles = pos.get("hold_candles", 0)
        pnl_pct = pos.get("pnl_pct", 0)
        hold_h = hold_candles * 15 / 60

        # 기본 추천: 모드 E = 4~6시간, 모드 D = 6~12시간
        if risk_score >= 60:
            optimal_h = 0  # 즉시 청산
            urgency = "high"
        elif mkt_risk == "DANGER":
            optimal_h = 2
            urgency = "high"
        elif pnl_pct > 2:
            # 수익 중: 더 보유 가능
            optimal_h = 12 if mkt_risk == "SAFE" else 6
            urgency = "low"
        elif pnl_pct < -1:
            # 손실 중: 보수적
            optimal_h = 4 if mkt_risk == "SAFE" else 2
            urgency = "medium"
        else:
            # 보합: 표준
            optimal_h = 8 if mkt_risk == "SAFE" else 4
            urgency = "low"

        remaining = max(optimal_h - hold_h, 0)

        # 타임아웃 임박 경고
        timeout_h = HOLD_TIMEOUT * 15 / 60
        if hold_h > timeout_h * 0.8:
            urgency = "high"

        return {
            "optimal_hold_h": round(optimal_h, 1),
            "remaining_h": round(remaining, 1),
            "current_hold_h": round(hold_h, 1),
            "urgency": urgency,
        }

    # ── SL/TP 조정 권고 ──

    @staticmethod
    def _recommend_sl_tp(pos: dict, mkt_risk: str, btc_data: dict) -> dict:
        """
        SL/TP 조정 권고.
        기반: 청산 거리, 시장 환경, 패턴 편향
        """
        liq_dist = pos.get("liquidation_distance_pct", 5.0)
        pnl_pct = pos.get("pnl_pct", 0)
        hold_candles = pos.get("hold_candles", 0)

        sl_action = "keep"
        tp_action = "keep"
        reasoning = "현 상태 유지"

        # SL 조정 판단
        if pnl_pct > 1.5 and hold_candles > HOLD_SHORT:
            # 수익 1.5% 이상 + 2시간 이상 → 본전 이동
            sl_action = "move_to_breakeven"
            reasoning = f"수익 {pnl_pct:+.1f}%, 본전 SL 이동 권고"
        elif mkt_risk == "DANGER" and liq_dist < LIQ_SAFE_PCT:
            # 시장 위험 + 여유 부족 → SL 조이기
            sl_action = "tighten"
            reasoning = f"시장 DANGER + 청산거리 {liq_dist:.1f}%, SL 조이기"
        elif liq_dist < LIQ_CAUTION_PCT:
            sl_action = "tighten"
            reasoning = f"청산거리 {liq_dist:.1f}% 위험, SL 조이기"

        # TP 조정 판단
        pattern_bias = btc_data.get("pattern_bias", "neutral")
        direction = pos.get("direction", "long")

        favorable = (
            (direction == "long" and pattern_bias == "bullish") or
            (direction == "short" and pattern_bias == "bearish")
        )

        if favorable and pnl_pct > 0.5:
            tp_action = "extend"
            reasoning += " | 패턴 유리 → TP 확장 검토"
        elif mkt_risk == "DANGER" and pnl_pct > 0:
            tp_action = "reduce"
            reasoning += " | 시장 위험 → 안전 TP로 축소"

        return {
            "sl_action": sl_action,
            "tp_action": tp_action,
            "reasoning": reasoning,
        }

    # ── 판결 근거 포맷 ──

    @staticmethod
    def _format_reasoning(pos: dict, score: int, mkt_risk: str, btc_bias: str) -> str:
        liq_dist = pos.get("liquidation_distance_pct", 5.0)
        pnl_pct = pos.get("pnl_pct", 0)
        hold_h = pos.get("hold_candles", 0) * 15 / 60

        parts = [f"리스크 {score}/100"]
        parts.append(f"청산거리 {liq_dist:.1f}%")
        parts.append(f"PnL {pnl_pct:+.1f}%")
        parts.append(f"보유 {hold_h:.1f}h")
        parts.append(f"시장 {mkt_risk}")

        direction = pos.get("direction", "long")
        if (direction == "long" and btc_bias == "bearish") or \
           (direction == "short" and btc_bias == "bullish"):
            parts.append(f"⚠️ BTC {btc_bias}와 역방향")

        return " | ".join(parts)

    # ── 반대 의견 생성 ──

    @staticmethod
    def _generate_dissent(pos: dict, score: int, btc_bias: str) -> str:
        """균형 잡힌 판단을 위한 반대 의견"""
        direction = pos.get("direction", "long")
        pnl_pct = pos.get("pnl_pct", 0)

        if score >= 60 and pnl_pct > 0:
            return "수익 중인 포지션을 청산하면 잠재 수익을 놓칠 수 있음"
        elif score < 30 and btc_bias == "neutral":
            return "BTC 방향 불명확 — 갑작스런 변동에 취약할 수 있음"
        elif score >= 40 and direction == "long" and btc_bias == "bullish":
            return "BTC 상승 추세이므로 Long은 유지할 가치가 있을 수 있음"
        elif score >= 40 and direction == "short" and btc_bias == "bearish":
            return "BTC 하락 추세이므로 Short은 유지할 가치가 있을 수 있음"
        return ""
