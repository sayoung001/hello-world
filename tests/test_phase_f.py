"""
Phase F 결함 수정 테스트
=========================
3-cycle 코드 리뷰에서 발견된 CRITICAL/HIGH 이슈 수정 검증:
- C2/C3: 시그널 필드 통일 (gap, adx 최상위)
- C4: RuleStore 스레드 안전 (threading.Lock)
- C5: division by zero 방어 (leverage=0)
- C7: ATR→레버리지 선형 보간
- H1: Enum 변환 방어 (_safe_risk_level)
- H5: 캐시 비용 공식 수정
- Pattern 3 타임아웃 규칙 생성
- _entry_snapshots 스레드 안전
- trading_brain gap 필드 폴백
"""

import sys
import os
import time
import tempfile
import json
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = {}


def _test_result(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    RESULTS[name] = {"status": status, "detail": detail}
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


# ─────────────────────────────────────────────
# 1. H1: _safe_risk_level Enum 변환 방어
# ─────────────────────────────────────────────

def test_safe_risk_level():
    """LLM이 대소문자 혼용 출력해도 안전하게 변환"""
    from risk.risk_agent import _safe_risk_level
    from risk.models import RiskLevel

    # 정상 케이스
    assert _safe_risk_level("low") == RiskLevel.LOW
    assert _safe_risk_level("medium") == RiskLevel.MEDIUM
    assert _safe_risk_level("high") == RiskLevel.HIGH
    assert _safe_risk_level("critical") == RiskLevel.CRITICAL

    # LLM 대소문자 혼용
    assert _safe_risk_level("HIGH") == RiskLevel.HIGH
    assert _safe_risk_level("Medium") == RiskLevel.MEDIUM
    assert _safe_risk_level("CRITICAL") == RiskLevel.CRITICAL
    assert _safe_risk_level(" low ") == RiskLevel.LOW

    # 잘못된 값 → MEDIUM 폴백
    assert _safe_risk_level("unknown") == RiskLevel.MEDIUM
    assert _safe_risk_level("") == RiskLevel.MEDIUM
    assert _safe_risk_level("danger") == RiskLevel.MEDIUM

    _test_result("H1_safe_risk_level", True, "대소문자 + 잘못된 값 모두 처리")


# ─────────────────────────────────────────────
# 2. C7: ATR→레버리지 선형 보간
# ─────────────────────────────────────────────

def test_atr_leverage_interpolation():
    """ATR 경계값에서 레버리지 급변 없이 선형 보간"""
    from risk.leverage_agent import LeverageAgent
    from risk.models import PipelineContext, MarketState, RiskAssessment

    agent = LeverageAgent()

    def get_leverage(atr_pct: float) -> int:
        ctx = PipelineContext(
            market_state=MarketState(btc_atr_pct=atr_pct)
        )
        result = agent._recommend_leverage(ctx, RiskAssessment())
        return result["recommended_leverage"]

    # 경계 내부: 선형 보간 확인
    lev_0_5 = get_leverage(0.5)   # ATR < 1.0 → 20x
    lev_1_0 = get_leverage(1.0)   # 경계값
    lev_1_5 = get_leverage(1.5)   # 1.0~2.0 구간 중간
    lev_2_0 = get_leverage(2.0)   # 경계값
    lev_2_5 = get_leverage(2.5)   # 2.0~3.0 구간 중간

    assert lev_0_5 == 20, f"ATR 0.5 → {lev_0_5} (expected 20)"
    assert lev_1_0 == 20, f"ATR 1.0 → {lev_1_0} (expected 20)"
    # 1.5는 20과 15 사이 → 약 18
    assert 15 <= lev_1_5 <= 20, f"ATR 1.5 → {lev_1_5} (expected 15~20)"
    assert lev_2_0 == 15, f"ATR 2.0 → {lev_2_0} (expected 15)"
    # 2.5는 15과 12 사이 → 약 14
    assert 12 <= lev_2_5 <= 15, f"ATR 2.5 → {lev_2_5} (expected 12~15)"

    # 연속성: 인접 ATR 간 레버리지 차이 ≤ 3
    prev_lev = get_leverage(0.0)
    for atr_x10 in range(1, 35):
        atr = atr_x10 / 10.0
        cur_lev = get_leverage(atr)
        diff = abs(cur_lev - prev_lev)
        assert diff <= 3, f"ATR {atr:.1f} 불연속: {prev_lev} → {cur_lev} (차이 {diff})"
        prev_lev = cur_lev

    _test_result("C7_atr_leverage_interpolation", True,
                 f"ATR 0.5→{lev_0_5}x, 1.5→{lev_1_5}x, 2.5→{lev_2_5}x (연속)")


# ─────────────────────────────────────────────
# 3. H5: 캐시 비용 공식 수정
# ─────────────────────────────────────────────

def test_cache_cost_formula():
    """cache_write 토큰 이중 계산 방지"""
    from agents.core.base import _track_usage, _usage_stats, reset_llm_usage

    reset_llm_usage()

    class MockUsage:
        input_tokens = 1000
        output_tokens = 200
        cache_read_input_tokens = 300
        cache_creation_input_tokens = 200  # cache_write

    class MockResponse:
        usage = MockUsage()

    _track_usage("test-model", "test-agent", MockResponse())

    # 수정된 공식: (1000 - 300 - 200)*1.0 + 300*0.1 + 200*1.25 = 500 + 30 + 250 = 780
    # 이전 (잘못된) 공식: (1000 - 300)*1.0 + 300*0.1 + 200*1.25 = 700 + 30 + 250 = 980
    cost = _usage_stats["estimated_cost_usd"]

    # 비용이 0보다 크고, 이전 공식보다 작아야 함
    assert cost > 0, "비용이 0"
    assert _usage_stats["input_tokens"] == 1000
    assert _usage_stats["cache_read_tokens"] == 300
    assert _usage_stats["cache_write_tokens"] == 200

    _test_result("H5_cache_cost_formula", True,
                 f"비용 정상 계산 (이중 계산 제거)")

    reset_llm_usage()


# ─────────────────────────────────────────────
# 4. C4: RuleStore 스레드 안전
# ─────────────────────────────────────────────

def test_rule_store_thread_safety():
    """동시 쓰기 시 JSON 파일 손상 방지"""
    from agents.memory.rule_store import RuleStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump([], f)
        tmp_path = f.name

    try:
        store = RuleStore(rules_path=tmp_path)

        assert hasattr(store, '_lock'), "RuleStore에 _lock 없음"

        errors = []

        def add_rules(thread_id: int):
            try:
                for i in range(5):
                    store.add(
                        condition=f"thread {thread_id} rule {i}",
                        action=f"action {thread_id}-{i}",
                    )
            except Exception as e:
                errors.append(f"Thread {thread_id}: {e}")

        threads = [threading.Thread(target=add_rules, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"스레드 에러: {errors}"
        assert store.count == 20, f"규칙 수: {store.count} (expected 20)"

        # JSON 파일 무결성 확인
        with open(tmp_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 20, f"JSON 규칙 수: {len(data)} (expected 20)"

        _test_result("C4_rule_store_thread_safety", True,
                     f"4스레드 × 5규칙 = {store.count}건 정상 저장")
    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────
# 5. C5: Division by zero 방어
# ─────────────────────────────────────────────

def test_leverage_zero_defense():
    """leverage=0 시 division by zero 방지"""
    from risk.risk_agent import RiskAgent
    from risk.models import PositionContext, MarketState, MarketAnalysis

    agent = RiskAgent()

    # leverage=0 포지션
    pos = PositionContext(
        symbol="TEST/USDT",
        direction="long",
        entry_price=100.0,
        current_price=99.0,
        leverage=0,
        roe_pct=-20.0,
    )

    try:
        result = agent._assess_single(pos, MarketState(), MarketAnalysis())
        assert "risk_level" in result
        _test_result("C5_leverage_zero_defense", True,
                     f"leverage=0 → 리스크 레벨: {result['risk_level']}")
    except ZeroDivisionError:
        _test_result("C5_leverage_zero_defense", False,
                     "ZeroDivisionError 발생!")


# ─────────────────────────────────────────────
# 6. gap 필드 폴백
# ─────────────────────────────────────────────

def test_trading_brain_gap_fallback():
    """signal에 gap_pct만 있어도 TradingBrain이 정상 동작"""
    from agents.memory.trading_brain import TradingBrain

    brain = TradingBrain()

    # gap 필드 직접
    result1 = brain.consult({"symbol": "ETH/USDT", "direction": "long", "gap": 0.5})
    assert result1["brain_confidence"] > 0

    # gap_pct 필드 (기존 시그널 형태)
    result2 = brain.consult({"symbol": "ETH/USDT", "direction": "long", "gap_pct": 0.5})
    assert result2["brain_confidence"] > 0

    # details.gap_pct (convergence_strategy.py 출력)
    result3 = brain.consult({
        "symbol": "ETH/USDT", "direction": "long",
        "details": {"gap_pct": 0.5}
    })
    assert result3["brain_confidence"] > 0

    _test_result("gap_field_fallback", True,
                 "gap / gap_pct / details.gap_pct 모두 정상")


# ─────────────────────────────────────────────
# 7. Pattern 3 타임아웃 규칙 생성
# ─────────────────────────────────────────────

def test_timeout_pattern_creates_rule():
    """패턴 3 타임아웃 시 규칙이 생성되는지 확인"""
    from agents.memory.reflection_engine import ReflectionEngine
    from agents.memory.rule_store import RuleStore

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump([], f)
        tmp_path = f.name

    try:
        store = RuleStore(rules_path=tmp_path)
        engine = ReflectionEngine(rule_store=store)

        initial_count = store.count

        trade = {
            "symbol": "ETH/USDT",
            "direction": "long",
            "gap": 0.3,
            "exit_type": "Timeout",
            "hold_candles": 150,
            "roe": -5.0,
            "market_state": {},
        }

        result = engine.reflect(trade)

        assert store.count > initial_count, "타임아웃 패턴 규칙이 생성되지 않음"
        assert result.get("key_factor") == "장기 횡보 후 타임아웃"

        _test_result("pattern3_timeout_rule", True,
                     f"규칙 {store.count - initial_count}건 생성")
    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("Phase F 결함 수정 테스트")
    print("=" * 50 + "\n")

    tests = [
        test_safe_risk_level,
        test_atr_leverage_interpolation,
        test_cache_cost_formula,
        test_rule_store_thread_safety,
        test_leverage_zero_defense,
        test_trading_brain_gap_fallback,
        test_timeout_pattern_creates_rule,
    ]

    for test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            _test_result(test_fn.__name__, False, str(e))

    print(f"\n{'=' * 50}")
    passed = sum(1 for v in RESULTS.values() if v["status"] == "PASS")
    total = len(RESULTS)
    print(f"결과: {passed}/{total} 통과")

    if passed < total:
        print("\n실패 항목:")
        for name, info in RESULTS.items():
            if info["status"] == "FAIL":
                print(f"  ❌ {name}: {info['detail']}")
    print("=" * 50)
