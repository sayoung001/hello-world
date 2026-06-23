"""
reflection_engine.py — 반성 엔진
==================================
거래 종료 후 자동 분석 → 교훈 도출 → 규칙 생성/갱신.
qrak 차용: 자동 반성 루프.

흐름:
1. 거래 종료 이벤트 수신
2. 진입 조건, 시장 상태, 결과를 수집
3. LLM에게 분석 요청 → 교훈 도출
4. 기존 규칙과 비교 → 강화/약화/신규 생성
"""

from __future__ import annotations
import json
import time

from agents.memory.rule_store import RuleStore

try:
    from agents.core.base import AgentBase, QUICK_MODEL
    _HAS_BASE = True
except ImportError:
    _HAS_BASE = False
    QUICK_MODEL = "claude-haiku-4-5-20251001"


REFLECTION_PROMPT = """거래 결과를 분석하고 실행 가능한 규칙을 도출하세요.

[진입 조건]
{entry_conditions}

[시장 상태]
{market_state}

[결과]
{trade_result}

다음을 도출하세요:
1. 이 거래가 성공/실패한 핵심 요인 (1~2문장)
2. 앞으로 유사 상황에서 적용할 규칙 (있다면, 최대 2개)
3. 규칙은 "조건 → 행동" 형태로 명확하게

JSON 형식으로만 응답:
{{
    "key_factor": "핵심 요인 설명",
    "new_rules": [
        {{"condition": "적용 조건", "action": "권장 행동", "confidence": 0.0~1.0}}
    ],
    "lesson_learned": "한줄 교훈"
}}"""


class ReflectionEngine:
    """
    거래 결과 → 분석 → 규칙 생성/갱신.

    LLM 사용 가능 시: LLM이 교훈 도출 → 규칙 자동 생성
    LLM 없을 시: 규칙 기반 패턴 매칭 → 간단한 규칙 생성
    """

    def __init__(self, rule_store: RuleStore | None = None,
                 llm_client=None):
        self.rule_store = rule_store or RuleStore()
        self._client = llm_client  # anthropic.Anthropic (선택)
        self._reflection_count = 0

    def reflect(self, trade_record: dict) -> dict:
        """
        거래 결과 분석 → 규칙 생성.

        Args:
            trade_record: {
                symbol, direction, entry_price, exit_price,
                exit_type (TP/SL/Timeout), roe, gap, confidence, adx,
                hold_candles, market_state: {btc_level, atr_pct, ...},
                timestamp,
            }

        Returns:
            {
                "key_factor": str,
                "new_rules": [TradingRule, ...],
                "lesson_learned": str,
                "method": "llm" | "rule_based",
            }
        """
        # LLM 분석 시도
        llm_result = self._llm_reflect(trade_record)
        if llm_result:
            return llm_result

        # 규칙 기반 폴백
        return self._rule_based_reflect(trade_record)

    def _llm_reflect(self, trade: dict) -> dict | None:
        """LLM 기반 반성"""
        if not self._client:
            # AgentBase에서 클라이언트 생성 시도
            try:
                import os
                import anthropic
                key = os.environ.get("ANTHROPIC_API_KEY", "")
                if not key:
                    return None
                self._client = anthropic.Anthropic(api_key=key)
            except Exception:
                return None

        try:
            entry_cond = (
                f"{trade.get('symbol', '?')} {trade.get('direction', '?')} 진입, "
                f"GAP {trade.get('gap', 0):.1f}%, "
                f"confidence {trade.get('confidence', 0)}, "
                f"ADX {trade.get('adx', 0):.0f}"
            )

            mkt = trade.get("market_state", {})
            market_state = (
                f"BTC 레벨 {mkt.get('btc_level', '?')}, "
                f"ATR {mkt.get('atr_pct', 0):.1f}%, "
                f"FGI {mkt.get('fear_greed', 50)}"
            )

            result = (
                f"결과: {trade.get('exit_type', '?')}, "
                f"ROE {trade.get('roe', 0):+.1f}%, "
                f"보유 {trade.get('hold_candles', 0)}캔들"
            )

            prompt = REFLECTION_PROMPT.format(
                entry_conditions=entry_cond,
                market_state=market_state,
                trade_result=result,
            )

            response = self._client.messages.create(
                model=QUICK_MODEL,
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text

            # JSON 파싱
            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0]
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0]

            parsed = json.loads(raw.strip())
            return self._process_llm_result(parsed, trade)

        except Exception as e:
            print(f"  [ReflectionEngine] LLM 반성 실패: {e}")
            return None

    def _process_llm_result(self, parsed: dict, trade: dict) -> dict:
        """LLM 결과를 규칙으로 변환"""
        created_rules = []
        for rule_data in parsed.get("new_rules", []):
            cond = rule_data.get("condition", "")
            action = rule_data.get("action", "")
            conf = float(rule_data.get("confidence", 0.5))

            if cond and action:
                rule = self.rule_store.add(
                    condition=cond,
                    action=action,
                    confidence=conf,
                    source="reflection",
                    metadata={
                        "trade_symbol": trade.get("symbol", ""),
                        "trade_exit": trade.get("exit_type", ""),
                        "trade_roe": trade.get("roe", 0),
                    },
                )
                created_rules.append(rule)

        self._reflection_count += 1

        return {
            "key_factor": parsed.get("key_factor", ""),
            "new_rules": created_rules,
            "lesson_learned": parsed.get("lesson_learned", ""),
            "method": "llm",
        }

    def _rule_based_reflect(self, trade: dict) -> dict:
        """규칙 기반 패턴 매칭 반성 (LLM 없을 때)"""
        symbol = trade.get("symbol", "?")
        direction = trade.get("direction", "?")
        exit_type = trade.get("exit_type", "?")
        roe = trade.get("roe", 0)
        gap = trade.get("gap", 0)
        hold = trade.get("hold_candles", 0)
        mkt = trade.get("market_state", {})

        rules_created = []
        key_factor = ""
        lesson = ""

        # 패턴 1: SL 히트 + 방향 맞음 (ROE가 양수였다가 음수)
        if exit_type == "SL" and trade.get("direction_correct", False):
            key_factor = "방향은 맞았으나 SL이 너무 타이트"
            lesson = f"{symbol} {direction}: SL 여유를 확대해야 함"
            rule = self.rule_store.add(
                condition=f"{symbol} {direction} GAP {gap:.1f}% 부근에서 SL 히트 + 방향 정확",
                action="SL 거리를 기존 대비 20% 확대 권장",
                confidence=0.5,
                source="reflection",
            )
            rules_created.append(rule)

        # 패턴 2: 빠른 TP (20캔들 이내)
        elif exit_type == "TP" and hold < 20:
            key_factor = "빠른 목표 도달"
            lesson = f"{symbol} GAP {gap:.1f}%: 빠른 수렴 이탈 패턴"
            rule = self.rule_store.add(
                condition=f"{symbol} GAP {gap:.1f}% 부근 {direction} 진입",
                action=f"빠른 이탈 예상 — TP 도달 시 부분 익절 후 트레일링 고려",
                confidence=0.45,
                source="reflection",
            )
            rules_created.append(rule)

        # 패턴 3: 장기 보유 + 타임아웃
        elif exit_type == "Timeout" and hold > 96:
            key_factor = "장기 횡보 후 타임아웃"
            lesson = f"{symbol}: 수렴 이탈 후 추세 미형성"
            rule = self.rule_store.add(
                condition=f"{symbol} GAP {gap:.1f}% {direction} — 96캔들 이상 횡보 패턴",
                action="스퀴즈 캔들 수 ≥ 15 또는 GAP ≤ 0.3% 시 진입 보류 고려",
                confidence=0.4,
                source="reflection",
            )
            rules_created.append(rule)

        # 패턴 4: BTC 레벨 높을 때 큰 손실
        elif roe < -50 and mkt.get("btc_level", 0) >= 4:
            key_factor = f"BTC 레벨 {mkt.get('btc_level')}에서 큰 손실"
            lesson = "BTC 고위험 구간에서 진입 자제"
            rule = self.rule_store.add(
                condition=f"BTC 레벨 {mkt.get('btc_level', 4)}+ 에서 {direction} 진입 시도",
                action="포지션 크기 50% 축소 또는 진입 보류",
                confidence=0.55,
                source="reflection",
            )
            rules_created.append(rule)

        else:
            if roe > 0:
                key_factor = "정상 수익 거래"
                lesson = f"{symbol} {direction}: GAP {gap:.1f}% 패턴 유효"
            else:
                key_factor = "일반적 손실"
                lesson = f"{symbol}: 시장 방향 불일치"

        self._reflection_count += 1

        return {
            "key_factor": key_factor,
            "new_rules": rules_created,
            "lesson_learned": lesson,
            "method": "rule_based",
        }

    @property
    def stats(self) -> dict:
        return {
            "total_reflections": self._reflection_count,
            "rules_in_store": self.rule_store.count,
        }
