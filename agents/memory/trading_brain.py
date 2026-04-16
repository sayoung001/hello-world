"""
trading_brain.py — 트레이딩 브레인
=====================================
qrak의 TradingBrain 컨셉 차용:
- 코인별/패턴별 승률 추적
- 시그널 발생 시 관련 규칙 조회 및 자문
- 규칙별 성과 기반 활성화/비활성화
"""

from __future__ import annotations
import time
from collections import defaultdict

from agents.memory.rule_store import RuleStore, TradingRule
from agents.memory.decay_engine import DecayEngine


class TradingBrain:
    """
    트레이딩 브레인 — 규칙 기반 자문 시스템.

    - 시그널 발생 시 관련 규칙을 검색하여 조언
    - 코인별/패턴별 승률을 추적
    - 시간 감쇠로 오래된 규칙 자동 약화
    """

    def __init__(self, rule_store: RuleStore | None = None,
                 decay_engine: DecayEngine | None = None):
        self.rule_store = rule_store or RuleStore()
        self.decay_engine = decay_engine or DecayEngine(self.rule_store)

        # 코인별/패턴별 승률 추적 (메모리 내)
        self._coin_stats: dict[str, dict] = defaultdict(
            lambda: {"wins": 0, "losses": 0, "total_roe": 0.0}
        )
        self._pattern_stats: dict[str, dict] = defaultdict(
            lambda: {"wins": 0, "losses": 0, "total_roe": 0.0}
        )

    def consult(self, signal: dict, market_state: dict | None = None) -> dict:
        """
        시그널 발생 시 관련 규칙 조회.

        Args:
            signal: {symbol, direction, gap, confidence, adx, ...}
            market_state: {btc_level, volatility, ...}

        Returns:
            {
                "applicable_rules": [규칙 정보 리스트],
                "coin_winrate": float,
                "pattern_winrate": float,
                "brain_confidence": float,
                "advice": str,
            }
        """
        symbol = signal.get("symbol", "")
        direction = signal.get("direction", "")
        gap = signal.get("gap", 0)

        # 관련 규칙 검색
        query = f"{symbol} {direction} GAP {gap:.1f}"
        if market_state:
            btc_level = market_state.get("btc_level", "")
            if btc_level:
                query += f" BTC 레벨 {btc_level}"

        rules = self.rule_store.search_by_condition(query, top_k=5)

        # 활성 + 가중치 순 정렬
        applicable = []
        for rule in rules:
            if not rule.active or rule.weight < 0.15:
                continue
            applicable.append({
                "rule_id": rule.rule_id,
                "condition": rule.condition,
                "action": rule.action,
                "confidence": round(rule.effective_confidence, 3),
                "sample_size": rule.sample_size,
                "hit_rate": round(rule.hit_rate, 3),
                "weight": round(rule.weight, 3),
                "age_days": round(rule.age_days, 1),
            })

        # 코인별 승률
        coin_stats = self._coin_stats.get(symbol, {"wins": 0, "losses": 0})
        coin_total = coin_stats["wins"] + coin_stats["losses"]
        coin_winrate = coin_stats["wins"] / coin_total if coin_total > 0 else 0.5

        # 패턴별 승률 (GAP 구간)
        gap_bin = f"gap_{int(gap * 10) / 10:.1f}"
        pattern_stats = self._pattern_stats.get(gap_bin, {"wins": 0, "losses": 0})
        pattern_total = pattern_stats["wins"] + pattern_stats["losses"]
        pattern_winrate = (
            pattern_stats["wins"] / pattern_total if pattern_total > 0 else 0.5
        )

        # 종합 신뢰도
        brain_confidence = self._compute_confidence(applicable, coin_winrate, pattern_winrate)

        # 조언 텍스트
        advice = self._generate_advice(applicable, coin_winrate, pattern_winrate, signal)

        return {
            "applicable_rules": applicable,
            "coin_winrate": round(coin_winrate, 4),
            "pattern_winrate": round(pattern_winrate, 4),
            "brain_confidence": round(brain_confidence, 4),
            "advice": advice,
        }

    def record_trade_result(self, symbol: str, direction: str,
                            gap: float, exit_type: str, roe: float):
        """
        거래 결과 기록 → 승률 업데이트.

        Args:
            symbol: 코인 심볼
            direction: "long" / "short"
            gap: EMA GAP
            exit_type: "TP" / "SL" / "Timeout"
            roe: 실현 ROE%
        """
        is_win = exit_type == "TP" or roe > 0

        # 코인별 통계
        stats = self._coin_stats[symbol]
        if is_win:
            stats["wins"] += 1
        else:
            stats["losses"] += 1
        stats["total_roe"] += roe

        # 패턴별 통계 (GAP 구간)
        gap_bin = f"gap_{int(gap * 10) / 10:.1f}"
        p_stats = self._pattern_stats[gap_bin]
        if is_win:
            p_stats["wins"] += 1
        else:
            p_stats["losses"] += 1
        p_stats["total_roe"] += roe

    def run_maintenance(self):
        """정기 유지보수: 감쇠 적용 (24시간 주기)"""
        if self.decay_engine.should_run_decay(interval_hours=24):
            result = self.decay_engine.decay_all()
            if result["deactivated"] > 0:
                print(f"  [TradingBrain] 감쇠 완료: "
                      f"{result['decayed']}건 감쇠, "
                      f"{result['deactivated']}건 비활성화")
            return result
        return None

    def _compute_confidence(self, rules: list, coin_wr: float,
                            pattern_wr: float) -> float:
        """종합 신뢰도 계산"""
        base = 0.4

        # 규칙 기반 보너스
        if rules:
            avg_rule_conf = sum(r["confidence"] for r in rules) / len(rules)
            base += avg_rule_conf * 0.2

        # 코인 승률 반영
        if coin_wr > 0.6:
            base += 0.1
        elif coin_wr < 0.4:
            base -= 0.1

        # 패턴 승률 반영
        if pattern_wr > 0.6:
            base += 0.1
        elif pattern_wr < 0.4:
            base -= 0.1

        return max(0.1, min(0.95, base))

    def _generate_advice(self, rules: list, coin_wr: float,
                         pattern_wr: float, signal: dict) -> str:
        """자문 텍스트 생성"""
        parts = []
        symbol = signal.get("symbol", "")

        if rules:
            top = rules[0]
            parts.append(f"관련 규칙: {top['condition']} → {top['action']} "
                         f"(신뢰도 {top['confidence']:.2f}, 적중 {top['hit_rate']:.0%})")

        if coin_wr != 0.5:
            parts.append(f"{symbol} 최근 승률: {coin_wr:.0%}")

        if pattern_wr != 0.5:
            gap = signal.get("gap", 0)
            parts.append(f"GAP {gap:.1f}% 구간 승률: {pattern_wr:.0%}")

        if not parts:
            return "참고할 과거 데이터 없음"

        return " | ".join(parts)

    def get_stats(self) -> dict:
        """브레인 통계"""
        rule_stats = self.rule_store.get_stats()
        return {
            "rules": rule_stats,
            "coins_tracked": len(self._coin_stats),
            "patterns_tracked": len(self._pattern_stats),
            "top_coins": sorted(
                [
                    {
                        "symbol": sym,
                        "winrate": s["wins"] / (s["wins"] + s["losses"])
                        if (s["wins"] + s["losses"]) > 0 else 0.5,
                        "trades": s["wins"] + s["losses"],
                    }
                    for sym, s in self._coin_stats.items()
                ],
                key=lambda x: x["trades"],
                reverse=True,
            )[:10],
        }
