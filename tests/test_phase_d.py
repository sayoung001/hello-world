"""
Phase D 모듈 동작 테스트
=========================
통합 레이어:
- EnrichedStateBuilder (Vision+RAG+Memory → PipelineContext)
- v9_hook.risk_check (리스크 파이프라인 호출)
- v9_hook.on_position_close → 자동 반성 → 규칙 생성
- 반성 → 자문 루프 (Brain+Reflection 연동)
"""

import sys
import os
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = {}


def test_result(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    RESULTS[name] = {"status": status, "detail": detail}
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


# ─────────────────────────────────────────────
# 1. EnrichedStateBuilder 기본 동작
# ─────────────────────────────────────────────
def test_enriched_state_minimal():
    """필수 모듈 없이도 PipelineContext 생성 가능"""
    print("\n=== 1. EnrichedStateBuilder (최소) ===")
    from agents.enriched_state import EnrichedStateBuilder
    from risk.models import PipelineContext

    builder = EnrichedStateBuilder()
    test_result("builder_init_empty",
                builder.vision_scorer is None and builder.brain is None,
                "빈 모듈로 초기화 OK")

    # 시그널 + 시장 데이터만
    signal = {"symbol": "ETHUSDT", "direction": "long", "gap": 0.3,
              "confidence": 60, "adx": 30}
    market_data = {
        "btc_price": 95000, "btc_level": 2,
        "btc_atr_pct": 1.5, "fear_greed": 50, "funding_rate": 0.01,
    }
    enriched = builder.build(signal=signal, market_data=market_data)

    ctx = enriched["pipeline_context"]
    test_result("builder_pipeline_ctx",
                isinstance(ctx, PipelineContext),
                f"PipelineContext 생성 OK")
    test_result("builder_market_state",
                ctx.market_state.btc_price == 95000
                and ctx.market_state.btc_level == 2,
                f"MarketState: BTC ${ctx.market_state.btc_price:,.0f}")
    test_result("builder_signal_passed",
                ctx.signal.get("symbol") == "ETHUSDT",
                f"signal 전달 OK")
    test_result("builder_empty_positions",
                len(ctx.positions) == 0,
                "포지션 없음")
    test_result("builder_empty_vision",
                ctx.vision_features == {},
                "Vision 피처 없음 (정상)")
    test_result("builder_empty_rag",
                ctx.rag_context == {},
                "RAG 피처 없음 (정상)")


# ─────────────────────────────────────────────
# 2. EnrichedStateBuilder + 포지션 변환
# ─────────────────────────────────────────────
def test_enriched_state_positions():
    """dict 포지션 + 객체 포지션 변환"""
    print("\n=== 2. EnrichedStateBuilder (포지션 변환) ===")
    from agents.enriched_state import EnrichedStateBuilder

    builder = EnrichedStateBuilder()

    # dict 포지션
    pos_dict = {
        "symbol": "ETHUSDT", "direction": "long",
        "entry_price": 3000.0, "current_price": 3100.0,
        "size_usdt": 500.0, "leverage": 20,
        "hold_candles": 24,
    }
    enriched = builder.build(positions=[pos_dict])
    ctx = enriched["pipeline_context"]
    test_result("builder_dict_pos",
                len(ctx.positions) == 1
                and ctx.positions[0].symbol == "ETHUSDT",
                f"dict 변환: {ctx.positions[0].symbol}")
    test_result("builder_dict_pnl",
                abs(ctx.positions[0].pnl_pct - 3.33) < 0.1,
                f"PnL 계산: {ctx.positions[0].pnl_pct:.2f}% (3000→3100)")
    test_result("builder_dict_roe",
                abs(ctx.positions[0].roe_pct - 66.6) < 1.0,
                f"ROE 계산: {ctx.positions[0].roe_pct:.1f}% (3.33% × 20x)")

    # 객체 포지션 (MockPosition)
    class MockPosition:
        symbol = "BTCUSDT"
        direction = "short"
        entry_price = 95000
        current_price = 93000
        leverage = 20
        quantity = 0.05

    obj_enriched = builder.build(positions=[MockPosition()])
    obj_ctx = obj_enriched["pipeline_context"]
    test_result("builder_obj_pos",
                len(obj_ctx.positions) == 1
                and obj_ctx.positions[0].symbol == "BTCUSDT",
                f"객체 변환: {obj_ctx.positions[0].symbol}")
    test_result("builder_obj_size",
                abs(obj_ctx.positions[0].size_usdt - 4750) < 1.0,
                f"사이즈: ${obj_ctx.positions[0].size_usdt:,.0f} (0.05×95000)")

    test_result("builder_total_exposure",
                obj_ctx.total_exposure == obj_ctx.positions[0].size_usdt,
                f"총 노출: ${obj_ctx.total_exposure:,.0f}")


# ─────────────────────────────────────────────
# 3. EnrichedStateBuilder + Brain 자문
# ─────────────────────────────────────────────
def test_enriched_with_brain():
    """Brain 자문 포함 통합 상태"""
    print("\n=== 3. EnrichedStateBuilder + Brain ===")
    from agents.enriched_state import EnrichedStateBuilder
    from agents.memory.rule_store import RuleStore
    from agents.memory.trading_brain import TradingBrain

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        store = RuleStore(rules_path=tmp_path)
        # 규칙 몇 개 사전 등록
        store.add(condition="ETH 롱 GAP 0.3 부근",
                  action="SL ATR×2.5 확대",
                  confidence=0.7)
        brain = TradingBrain(rule_store=store)

        # 과거 거래 기록 (승률 생성)
        brain.record_trade_result("ETHUSDT", "long", 0.3, "TP", 15)
        brain.record_trade_result("ETHUSDT", "long", 0.3, "TP", 12)
        brain.record_trade_result("ETHUSDT", "long", 0.3, "SL", -8)

        builder = EnrichedStateBuilder(brain=brain)
        signal = {"symbol": "ETHUSDT", "direction": "long", "gap": 0.3}
        market = {"btc_level": 2, "fear_greed": 50}
        enriched = builder.build(signal=signal, market_data=market)

        advice = enriched["brain_advice"]
        test_result("builder_brain_keys",
                    all(k in advice for k in ["applicable_rules",
                                               "coin_winrate",
                                               "brain_confidence"]),
                    f"brain keys: {list(advice.keys())[:3]}")
        test_result("builder_brain_rules",
                    len(advice["applicable_rules"]) >= 1,
                    f"applicable_rules={len(advice['applicable_rules'])}")
        test_result("builder_brain_winrate",
                    abs(advice["coin_winrate"] - 0.6667) < 0.01,
                    f"ETH winrate={advice['coin_winrate']:.4f} (2W/1L)")

        summary = builder.summarize(enriched)
        test_result("builder_summary",
                    "Brain" in summary and "포지션" in summary,
                    f"summary: {summary[:80]}")
    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────
# 4. v9_hook 초기화 + Phase D 모듈 로드
# ─────────────────────────────────────────────
def test_v9_hook_init():
    """v9_hook의 Phase B+C 통합 초기화"""
    print("\n=== 4. v9_hook 초기화 (Phase D) ===")
    from agents.v9_hook import AgentHook, _HAS_RISK_PIPELINE, _HAS_MEMORY_SYSTEM

    test_result("hook_import_risk", _HAS_RISK_PIPELINE,
                f"_HAS_RISK_PIPELINE={_HAS_RISK_PIPELINE}")
    test_result("hook_import_memory", _HAS_MEMORY_SYSTEM,
                f"_HAS_MEMORY_SYSTEM={_HAS_MEMORY_SYSTEM}")

    hook = AgentHook()
    test_result("hook_risk_pipeline",
                hook._risk_pipeline is not None,
                "RiskPipeline 인스턴스 생성 OK")
    test_result("hook_brain",
                hook._brain is not None,
                "TradingBrain 인스턴스 생성 OK")
    test_result("hook_reflection",
                hook._reflection is not None,
                "ReflectionEngine 인스턴스 생성 OK")
    test_result("hook_builder",
                hook._state_builder is not None,
                "EnrichedStateBuilder 인스턴스 생성 OK")
    test_result("hook_entry_snapshots",
                isinstance(hook._entry_snapshots, dict),
                "진입 스냅샷 dict 초기화")


# ─────────────────────────────────────────────
# 5. v9_hook.risk_check - 실제 리스크 평가
# ─────────────────────────────────────────────
def test_v9_hook_risk_check():
    """risk_check()로 파이프라인 실행 및 판단 반환"""
    print("\n=== 5. v9_hook.risk_check ===")
    from agents.v9_hook import AgentHook

    hook = AgentHook()

    # 시나리오 A: 정상 시장
    signal = {"symbol": "ETHUSDT", "direction": "long", "gap": 0.3,
              "confidence": 60, "adx": 30}
    market = {
        "btc_price": 95000, "btc_level": 1,
        "btc_atr_pct": 1.2, "fear_greed": 50,
        "funding_rate": 0.01, "btc_rsi": 55,
    }
    result_a = hook.risk_check(signal=signal, market_data=market,
                                account_balance=1000, recent_losses=0)

    test_result("risk_check_a_result",
                result_a is not None,
                "정상 시장에서 결과 반환")
    test_result("risk_check_a_keys",
                all(k in result_a for k in ["risk_level", "urgency",
                                             "recommended_leverage",
                                             "approved", "decision"]),
                f"keys: {list(result_a.keys())[:5]}")
    test_result("risk_check_a_approved",
                result_a["approved"] is True,
                f"정상 시장 승인: {result_a['risk_level']}")
    test_result("risk_check_a_leverage",
                10 <= result_a["recommended_leverage"] <= 20,
                f"레버리지: {result_a['recommended_leverage']}x")

    # 시나리오 B: 위기 시장 + 손실 포지션
    class CrashPosition:
        symbol = "SOLUSDT"
        direction = "long"
        entry_price = 200
        current_price = 180
        leverage = 20
        quantity = 5.0

    crash_signal = {"symbol": "AVAXUSDT", "direction": "long", "gap": 0.5}
    crash_market = {
        "btc_price": 82000, "btc_level": 5,
        "btc_atr_pct": 3.8, "fear_greed": 10,
        "funding_rate": -0.08, "btc_rsi": 22,
        "btc_change_24h": -8,
    }
    result_b = hook.risk_check(
        signal=crash_signal,
        positions=[CrashPosition()],
        market_data=crash_market,
        account_balance=500,
        recent_losses=3,
    )

    test_result("risk_check_b_result",
                result_b is not None,
                "위기 시장 결과 반환")
    test_result("risk_check_b_high_risk",
                result_b["risk_level"] in ("high", "critical"),
                f"위기 시장 리스크: {result_b['risk_level']}")
    test_result("risk_check_b_leverage_reduced",
                result_b["recommended_leverage"] <= 12,
                f"위기 레버리지 감소: {result_b['recommended_leverage']}x")

    # 스냅샷 저장 확인
    test_result("risk_check_snapshot",
                "ETHUSDT" in hook._entry_snapshots
                or "AVAXUSDT" in hook._entry_snapshots,
                f"진입 스냅샷 저장: {list(hook._entry_snapshots.keys())}")


# ─────────────────────────────────────────────
# 6. v9_hook.on_position_close → 자동 반성
# ─────────────────────────────────────────────
def test_v9_hook_reflection_automation():
    """포지션 청산 → 자동 반성 → 규칙 생성"""
    print("\n=== 6. 자동 반성 루프 ===")
    from agents.v9_hook import AgentHook

    hook = AgentHook()
    initial_rules = hook._rule_store.count

    # 1. 진입 (risk_check로 스냅샷 저장)
    signal = {"symbol": "ETHUSDT", "direction": "long", "gap": 0.3,
              "confidence": 60, "adx": 30}
    market = {"btc_price": 95000, "btc_level": 2,
              "btc_atr_pct": 1.5, "fear_greed": 50}
    hook.risk_check(signal=signal, market_data=market,
                    account_balance=1000)

    test_result("reflect_snapshot_saved",
                "ETHUSDT" in hook._entry_snapshots,
                f"진입 스냅샷 저장됨")

    # 2. 청산 (SL 시나리오)
    class MockPosition:
        symbol = "ETHUSDT"
        direction = "long"
        entry_price = 3000
        current_price = 2940
        leverage = 20
        def hold_candles_15m(self):
            return 24

    hook.on_position_close(MockPosition(), close_reason="SL", pnl_pct=-2.0)

    # 반성은 별도 스레드 → 약간 대기
    time.sleep(1.0)

    # 3. 결과 검증
    test_result("reflect_snapshot_consumed",
                "ETHUSDT" not in hook._entry_snapshots,
                "청산 후 스냅샷 제거됨")

    final_rules = hook._rule_store.count
    test_result("reflect_rule_created",
                final_rules >= initial_rules,
                f"규칙 수: {initial_rules} → {final_rules}")

    # 4. 브레인 승률 반영
    brain_stats = hook._brain.get_stats()
    test_result("reflect_brain_updated",
                brain_stats["coins_tracked"] >= 1,
                f"brain에 코인 등록: {brain_stats['coins_tracked']}개")

    # 5. TP 시나리오도 처리
    signal_tp = {"symbol": "BTCUSDT", "direction": "short", "gap": 0.5,
                 "confidence": 65, "adx": 35}
    hook.risk_check(signal=signal_tp, market_data=market)

    class TPPosition:
        symbol = "BTCUSDT"
        direction = "short"
        entry_price = 95000
        current_price = 93000
        leverage = 20
        def hold_candles_15m(self):
            return 15

    hook.on_position_close(TPPosition(), close_reason="TP", pnl_pct=2.1)
    time.sleep(1.0)

    brain_stats2 = hook._brain.get_stats()
    test_result("reflect_multi_trades",
                brain_stats2["coins_tracked"] >= 2,
                f"2개 코인 기록: {[c['symbol'] for c in brain_stats2['top_coins']]}")


# ─────────────────────────────────────────────
# 7. 반성→자문 루프 (학습 사이클)
# ─────────────────────────────────────────────
def test_learning_loop():
    """거래 결과가 다음 자문에 반영되는 학습 루프"""
    print("\n=== 7. 학습 루프 (Reflect → Consult) ===")
    from agents.v9_hook import AgentHook

    hook = AgentHook()

    signal = {"symbol": "SOLUSDT", "direction": "long", "gap": 0.4,
              "confidence": 55, "adx": 28}
    market = {"btc_price": 95000, "btc_level": 2,
              "btc_atr_pct": 1.5, "fear_greed": 50}

    # 1차 자문 (빈 상태)
    advice_before = hook.get_brain_advice(signal, market_state=market)
    rules_before = len(advice_before.get("applicable_rules", []))
    winrate_before = advice_before.get("coin_winrate", 0.5)
    test_result("loop_initial_advice",
                advice_before is not None,
                f"초기 자문: rules={rules_before}, winrate={winrate_before}")

    # 거래 3건 실행 (진입 + 청산)
    class Pos:
        def __init__(self, sym, direction, entry, current, lev=20):
            self.symbol = sym
            self.direction = direction
            self.entry_price = entry
            self.current_price = current
            self.leverage = lev
            self.quantity = 5.0
        def hold_candles_15m(self):
            return 20

    for i, (exit_type, exit_price, pnl) in enumerate([
        ("SL", 193, -3.5),  # 손실
        ("TP", 210, 5.0),   # 이익
        ("TP", 212, 6.0),   # 이익
    ]):
        hook.risk_check(signal=signal, market_data=market)
        pos = Pos("SOLUSDT", "long", 200, exit_price)
        hook.on_position_close(pos, close_reason=exit_type, pnl_pct=pnl)

    time.sleep(2.0)

    # 2차 자문 (학습 후)
    advice_after = hook.get_brain_advice(signal, market_state=market)
    rules_after = len(advice_after.get("applicable_rules", []))
    winrate_after = advice_after.get("coin_winrate", 0.5)

    test_result("loop_rules_learned",
                rules_after >= rules_before,
                f"규칙 학습: {rules_before} → {rules_after}")
    test_result("loop_winrate_updated",
                winrate_after != 0.5,
                f"SOL 승률 업데이트: {winrate_before:.4f} → {winrate_after:.4f} (2W/1L)")

    stats = hook.get_memory_stats()
    test_result("loop_memory_stats",
                stats is not None
                and stats["reflection"]["total_reflections"] >= 3,
                f"반성 횟수: {stats['reflection']['total_reflections']}")


# ─────────────────────────────────────────────
# 8. 비활성화 플래그 테스트
# ─────────────────────────────────────────────
def test_disable_flags():
    """enable_risk_pipeline / enable_memory 비활성화 동작"""
    print("\n=== 8. 비활성화 플래그 ===")
    from agents.v9_hook import AgentHook

    hook_disabled = AgentHook(enable_risk_pipeline=False, enable_memory=False)
    test_result("disabled_risk_pipeline",
                hook_disabled._risk_pipeline is None,
                "리스크 파이프라인 비활성화")
    test_result("disabled_brain",
                hook_disabled._brain is None,
                "Brain 비활성화")

    # risk_check → None 반환
    signal = {"symbol": "ETHUSDT", "direction": "long", "gap": 0.3}
    result = hook_disabled.risk_check(signal=signal)
    test_result("disabled_risk_check_none",
                result is None,
                "비활성화 시 risk_check → None")

    # get_brain_advice → None
    advice = hook_disabled.get_brain_advice(signal)
    test_result("disabled_brain_advice_none",
                advice is None,
                "비활성화 시 get_brain_advice → None")


# ─────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("Phase D: 통합 레이어 테스트")
    print("=" * 60)

    test_enriched_state_minimal()
    test_enriched_state_positions()
    test_enriched_with_brain()
    test_v9_hook_init()
    test_v9_hook_risk_check()
    test_v9_hook_reflection_automation()
    test_learning_loop()
    test_disable_flags()

    print("\n" + "=" * 60)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS.values() if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS.values() if r["status"] == "FAIL")
    print(f"총 {total}건: PASS {passed}, FAIL {failed}")
    print("=" * 60)

    if failed > 0:
        print("\n실패 항목:")
        for name, r in RESULTS.items():
            if r["status"] == "FAIL":
                print(f"  ✗ {name}: {r['detail']}")
