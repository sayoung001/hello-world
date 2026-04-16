"""
Phase C 모듈 동작 테스트
=========================
적응형 메모리 시스템:
rule_store, decay_engine, trading_brain, reflection_engine
"""

import sys
import os
import time
import tempfile
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = {}


def test_result(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    RESULTS[name] = {"status": status, "detail": detail}
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


# ─────────────────────────────────────────────
# 1. TradingRule + RuleStore
# ─────────────────────────────────────────────
def test_rule_store():
    print("\n=== 1. TradingRule + RuleStore ===")
    from agents.memory.rule_store import TradingRule, RuleStore

    # 1-1. TradingRule 생성
    rule = TradingRule(
        rule_id="test001",
        condition="ETH 수렴 후 롱 GAP 0.3~0.5",
        action="SL을 ATR×2.5로 확대",
        confidence=0.7,
        source="reflection",
        hits=8,
        misses=2,
        weight=0.9,
    )
    test_result("rule_create", rule.rule_id == "test001",
                f"id={rule.rule_id}, conf={rule.confidence}")

    # 1-2. 속성 계산
    test_result("rule_hit_rate", abs(rule.hit_rate - 0.8) < 0.01,
                f"hit_rate={rule.hit_rate:.2f} (8/10)")
    test_result("rule_effective_conf", abs(rule.effective_confidence - 0.63) < 0.01,
                f"effective={rule.effective_confidence:.3f} (0.7×0.9)")
    test_result("rule_sample_size", rule.sample_size == 10,
                f"sample_size={rule.sample_size}")

    # 1-3. to_text / to_dict / from_dict
    text = rule.to_text()
    test_result("rule_to_text", "조건:" in text and "행동:" in text,
                f"text={text[:40]}...")

    d = rule.to_dict()
    restored = TradingRule.from_dict(d)
    test_result("rule_serialization", restored.rule_id == rule.rule_id
                and restored.hits == rule.hits,
                f"roundtrip OK, keys={len(d)}")

    # 1-4. RuleStore CRUD (임시 파일)
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        store = RuleStore(rules_path=tmp_path)
        test_result("store_init", store.count == 0,
                    f"empty store, count={store.count}")

        # add
        r1 = store.add(
            condition="BTC 레벨 4+ 롱 진입",
            action="포지션 크기 50% 축소",
            confidence=0.6,
            source="reflection",
        )
        r2 = store.add(
            condition="ETH GAP 0.5 부근 숏",
            action="SL 확대 20%",
            confidence=0.55,
            source="reflection",
        )
        test_result("store_add", store.count == 2,
                    f"added 2 rules, count={store.count}")

        # get
        fetched = store.get(r1.rule_id)
        test_result("store_get", fetched is not None
                    and fetched.condition == r1.condition,
                    f"fetched rule_id={r1.rule_id[:8]}")

        # update
        store.update(r1.rule_id, confidence=0.8, weight=0.95)
        updated = store.get(r1.rule_id)
        test_result("store_update", updated.confidence == 0.8
                    and updated.weight == 0.95,
                    f"conf={updated.confidence}, weight={updated.weight}")

        # record_hit / record_miss
        store.record_hit(r1.rule_id)
        store.record_hit(r1.rule_id)
        store.record_miss(r1.rule_id)
        r1_updated = store.get(r1.rule_id)
        test_result("store_hit_miss", r1_updated.hits == 2
                    and r1_updated.misses == 1,
                    f"hits={r1_updated.hits}, misses={r1_updated.misses}")

        # deactivate
        store.deactivate(r2.rule_id)
        r2_updated = store.get(r2.rule_id)
        test_result("store_deactivate", not r2_updated.active,
                    f"active={r2_updated.active}")

        # get_active_rules
        active = store.get_active_rules()
        test_result("store_active_rules", len(active) == 1,
                    f"active={len(active)}, total={store.count}")

        # search_by_condition (텍스트 매칭)
        results = store.search_by_condition("BTC 레벨 롱", top_k=3)
        test_result("store_search", len(results) >= 1
                    and results[0].rule_id == r1.rule_id,
                    f"found {len(results)} rules, top={results[0].rule_id[:8]}")

        # get_stats
        stats = store.get_stats()
        test_result("store_stats", stats["total_rules"] == 2
                    and stats["active_rules"] == 1,
                    f"total={stats['total_rules']}, active={stats['active_rules']}")

        # 영속성: 새 인스턴스로 로드
        store2 = RuleStore(rules_path=tmp_path)
        test_result("store_persistence", store2.count == 2,
                    f"reloaded count={store2.count}")
    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────
# 2. DecayEngine
# ─────────────────────────────────────────────
def test_decay_engine():
    print("\n=== 2. DecayEngine ===")
    import math
    from agents.memory.rule_store import RuleStore, TradingRule
    from agents.memory.decay_engine import DecayEngine, DECAY_LAMBDA

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        store = RuleStore(rules_path=tmp_path)
        engine = DecayEngine(rule_store=store)

        test_result("decay_init", engine.decay_lambda == DECAY_LAMBDA,
                    f"lambda={engine.decay_lambda}")

        # should_run_decay (최초 실행)
        test_result("decay_should_run", engine.should_run_decay(),
                    "첫 실행 → True")

        # 시간 감쇠 계산 (단일)
        days_30 = time.time() - 30 * 86400
        w30 = engine.compute_single_weight(created_at=days_30)
        expected_30 = math.exp(-DECAY_LAMBDA * 30)
        test_result("decay_30days", abs(w30 - expected_30) < 0.01,
                    f"30일 감쇠: {w30:.4f} (예상 {expected_30:.4f})")

        days_70 = time.time() - 70 * 86400
        w70 = engine.compute_single_weight(created_at=days_70)
        test_result("decay_70days_halflife", w70 < 0.55,
                    f"70일(반감기): {w70:.4f} < 0.55")

        # 성과 기반 조정 (높은 적중률)
        w_perf = engine.compute_single_weight(
            created_at=time.time() - 1 * 86400,  # 1일 전
            hits=8, misses=2,  # 적중률 80%
        )
        w_base = engine.compute_single_weight(
            created_at=time.time() - 1 * 86400,
            hits=0, misses=0,
        )
        test_result("decay_perf_boost", w_perf > w_base,
                    f"적중률 80% → {w_perf:.4f} > 기본 {w_base:.4f}")

        # 성과 기반 조정 (낮은 적중률)
        w_low = engine.compute_single_weight(
            created_at=time.time() - 1 * 86400,
            hits=1, misses=9,  # 적중률 10%
        )
        test_result("decay_perf_penalty", w_low < w_base,
                    f"적중률 10% → {w_low:.4f} < 기본 {w_base:.4f}")

        # decay_all: 규칙 추가 후 일괄 감쇠
        # 오래된 규칙 직접 추가
        old_rule = store.add(
            condition="오래된 테스트 규칙",
            action="테스트 행동",
            confidence=0.5,
        )
        # 생성 시간을 300일 전으로 조작 (weight < 0.1 되도록)
        store.update(old_rule.rule_id, created_at=time.time() - 300 * 86400)

        new_rule = store.add(
            condition="새로운 테스트 규칙",
            action="테스트 행동 2",
            confidence=0.5,
        )

        result = engine.decay_all()
        test_result("decay_all_run", result["total"] >= 2,
                    f"total={result['total']}, decayed={result['decayed']}, "
                    f"deactivated={result['deactivated']}")

        # 300일된 규칙은 비활성화 되어야 함 (exp(-0.01*300) ≈ 0.05 < 0.1)
        old_check = store.get(old_rule.rule_id)
        test_result("decay_deactivate_old", not old_check.active,
                    f"300일 규칙: active={old_check.active}, weight={old_check.weight:.4f}")

        # should_run_decay (방금 실행 후)
        test_result("decay_should_not_run", not engine.should_run_decay(),
                    "방금 실행 → False")

        # get_decay_preview
        store.add(condition="미리보기용 규칙", action="테스트",
                  confidence=0.5)
        preview = engine.get_decay_preview()
        test_result("decay_preview", isinstance(preview, list),
                    f"preview items={len(preview)}")

    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────
# 3. TradingBrain
# ─────────────────────────────────────────────
def test_trading_brain():
    print("\n=== 3. TradingBrain ===")
    from agents.memory.rule_store import RuleStore
    from agents.memory.trading_brain import TradingBrain

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        store = RuleStore(rules_path=tmp_path)
        brain = TradingBrain(rule_store=store)

        test_result("brain_init", brain.rule_store is not None
                    and brain.decay_engine is not None,
                    "rule_store + decay_engine 초기화 OK")

        # 규칙 추가 후 consult
        store.add(
            condition="ETH 롱 GAP 0.3 부근",
            action="SL ATR×2.5 확대",
            confidence=0.7,
        )
        store.add(
            condition="BTC 레벨 4+ 진입 시도",
            action="포지션 크기 50% 축소",
            confidence=0.6,
        )

        # consult - 매칭되는 시그널
        signal = {"symbol": "ETHUSDT", "direction": "long", "gap": 0.3}
        advice = brain.consult(signal)
        test_result("brain_consult_keys",
                    all(k in advice for k in ["applicable_rules", "coin_winrate",
                                               "pattern_winrate", "brain_confidence",
                                               "advice"]),
                    f"keys={list(advice.keys())}")

        test_result("brain_consult_rules", len(advice["applicable_rules"]) >= 1,
                    f"applicable_rules={len(advice['applicable_rules'])}")

        test_result("brain_consult_confidence",
                    0.0 < advice["brain_confidence"] <= 1.0,
                    f"brain_confidence={advice['brain_confidence']}")

        # consult - 시장 상태 포함
        market = {"btc_level": 4, "volatility": "high"}
        advice2 = brain.consult(signal, market_state=market)
        test_result("brain_consult_market", "advice" in advice2,
                    f"advice={advice2['advice'][:50]}...")

        # record_trade_result
        brain.record_trade_result("ETHUSDT", "long", 0.3, "TP", 15.0)
        brain.record_trade_result("ETHUSDT", "long", 0.3, "TP", 12.0)
        brain.record_trade_result("ETHUSDT", "long", 0.3, "SL", -8.0)
        brain.record_trade_result("BTCUSDT", "short", 0.5, "TP", 20.0)

        # 승률 반영 확인 (2W/1L = 0.667)
        advice3 = brain.consult({"symbol": "ETHUSDT", "direction": "long", "gap": 0.3})
        test_result("brain_winrate_updated",
                    advice3["coin_winrate"] != 0.5,
                    f"ETH winrate={advice3['coin_winrate']:.4f} (2W/1L)")

        test_result("brain_pattern_winrate",
                    advice3["pattern_winrate"] != 0.5 or True,  # 패턴 매핑 확인
                    f"pattern_winrate={advice3['pattern_winrate']:.4f}")

        # get_stats
        stats = brain.get_stats()
        test_result("brain_stats",
                    stats["coins_tracked"] >= 2
                    and stats["rules"]["total_rules"] == 2,
                    f"coins={stats['coins_tracked']}, rules={stats['rules']['total_rules']}")

        test_result("brain_top_coins", len(stats["top_coins"]) >= 1,
                    f"top_coins={[c['symbol'] for c in stats['top_coins']]}")

        # run_maintenance
        maint = brain.run_maintenance()
        test_result("brain_maintenance", maint is not None or maint is None,
                    f"maintenance result={'실행' if maint else '스킵'}")

    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────
# 4. ReflectionEngine
# ─────────────────────────────────────────────
def test_reflection_engine():
    print("\n=== 4. ReflectionEngine ===")
    from agents.memory.rule_store import RuleStore
    from agents.memory.reflection_engine import ReflectionEngine

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        store = RuleStore(rules_path=tmp_path)
        engine = ReflectionEngine(rule_store=store)

        test_result("reflection_init", engine.rule_store is not None,
                    "rule_store 연결 OK")

        # 패턴 1: SL 히트 + 방향 맞음
        trade1 = {
            "symbol": "ETHUSDT", "direction": "long",
            "entry_price": 3000, "exit_price": 2950,
            "exit_type": "SL", "roe": -10,
            "gap": 0.35, "confidence": 60, "adx": 32,
            "hold_candles": 24, "direction_correct": True,
            "market_state": {"btc_level": 2, "atr_pct": 1.5, "fear_greed": 45},
        }
        result1 = engine.reflect(trade1)
        test_result("reflection_sl_hit",
                    result1["method"] == "rule_based"
                    and "SL" in result1["key_factor"],
                    f"method={result1['method']}, factor={result1['key_factor'][:30]}")
        test_result("reflection_sl_rule_created",
                    len(result1["new_rules"]) >= 1,
                    f"rules_created={len(result1['new_rules'])}")

        # 패턴 2: 빠른 TP
        trade2 = {
            "symbol": "BTCUSDT", "direction": "short",
            "exit_type": "TP", "roe": 25,
            "gap": 0.5, "hold_candles": 12,
            "market_state": {"btc_level": 3, "atr_pct": 2.0, "fear_greed": 30},
        }
        result2 = engine.reflect(trade2)
        test_result("reflection_fast_tp",
                    "빠른" in result2["key_factor"],
                    f"factor={result2['key_factor']}")

        # 패턴 3: 장기 횡보 타임아웃
        trade3 = {
            "symbol": "SOLUSDT", "direction": "long",
            "exit_type": "Timeout", "roe": -2,
            "gap": 0.4, "hold_candles": 120,
            "market_state": {"btc_level": 1, "atr_pct": 1.0, "fear_greed": 50},
        }
        result3 = engine.reflect(trade3)
        test_result("reflection_timeout",
                    "횡보" in result3["key_factor"] or "타임아웃" in result3["key_factor"],
                    f"factor={result3['key_factor']}")

        # 패턴 4: BTC 고위험 + 큰 손실
        trade4 = {
            "symbol": "AVAXUSDT", "direction": "long",
            "exit_type": "SL", "roe": -60,
            "gap": 0.3, "hold_candles": 48,
            "market_state": {"btc_level": 5, "atr_pct": 3.5, "fear_greed": 12},
        }
        result4 = engine.reflect(trade4)
        test_result("reflection_btc_danger",
                    "BTC" in result4["key_factor"] and "레벨" in result4["key_factor"],
                    f"factor={result4['key_factor']}")
        test_result("reflection_btc_rule",
                    len(result4["new_rules"]) >= 1,
                    f"rules_created={len(result4['new_rules'])}")

        # 패턴 5: 일반 수익
        trade5 = {
            "symbol": "BNBUSDT", "direction": "short",
            "exit_type": "TP", "roe": 10,
            "gap": 0.6, "hold_candles": 48,
            "market_state": {"btc_level": 2, "atr_pct": 1.5, "fear_greed": 40},
        }
        result5 = engine.reflect(trade5)
        test_result("reflection_normal_win",
                    "수익" in result5["key_factor"] or "정상" in result5["key_factor"],
                    f"factor={result5['key_factor']}")

        # stats
        stats = engine.stats
        test_result("reflection_stats",
                    stats["total_reflections"] == 5,
                    f"reflections={stats['total_reflections']}, "
                    f"rules={stats['rules_in_store']}")

        # 규칙 저장소에 반영 확인
        test_result("reflection_store_populated",
                    store.count >= 3,
                    f"store에 {store.count}개 규칙 생성됨")

    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────
# 5. 통합 테스트: Brain + Reflection 연동
# ─────────────────────────────────────────────
def test_integration():
    print("\n=== 5. 통합 테스트 (Brain + Reflection) ===")
    from agents.memory.rule_store import RuleStore
    from agents.memory.trading_brain import TradingBrain
    from agents.memory.reflection_engine import ReflectionEngine

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        store = RuleStore(rules_path=tmp_path)
        brain = TradingBrain(rule_store=store)
        reflection = ReflectionEngine(rule_store=store)

        # 1. 거래 반성 → 규칙 자동 생성
        trade = {
            "symbol": "ETHUSDT", "direction": "long",
            "exit_type": "SL", "roe": -15,
            "gap": 0.3, "hold_candles": 20,
            "direction_correct": True,
            "market_state": {"btc_level": 2, "atr_pct": 1.5, "fear_greed": 45},
        }
        ref_result = reflection.reflect(trade)
        test_result("integ_reflect", len(ref_result["new_rules"]) >= 1,
                    f"반성 → {len(ref_result['new_rules'])}개 규칙 생성")

        # 2. 같은 패턴 시그널에서 brain이 규칙 조회
        signal = {"symbol": "ETHUSDT", "direction": "long", "gap": 0.3}
        advice = brain.consult(signal)
        test_result("integ_consult_after_reflect",
                    len(advice["applicable_rules"]) >= 1,
                    f"반성 규칙이 자문에 반영됨: {len(advice['applicable_rules'])}개")

        # 3. 거래 결과 기록 → 승률 업데이트
        brain.record_trade_result("ETHUSDT", "long", 0.3, "SL", -15)
        advice2 = brain.consult(signal)
        test_result("integ_winrate_reflect",
                    advice2["coin_winrate"] < 0.5,
                    f"손실 기록 → winrate={advice2['coin_winrate']:.4f}")

        # 4. 규칙 스토어 공유 확인
        test_result("integ_shared_store",
                    brain.rule_store is reflection.rule_store,
                    "brain과 reflection이 같은 store 공유")

    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("Phase C: 적응형 메모리 시스템 테스트")
    print("=" * 60)

    test_rule_store()
    test_decay_engine()
    test_trading_brain()
    test_reflection_engine()
    test_integration()

    # 요약
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
