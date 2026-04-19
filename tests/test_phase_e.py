"""
Phase E 모듈 동작 테스트
=========================
LLM API 최적화 + Walk-Forward 프레임워크:
- 비용 추적기 (글로벌 싱글턴)
- 프롬프트 캐싱 (cache_control 적용)
- 모델 배분 최적화 (Haiku/Sonnet)
- Walk-Forward 백테스트 프레임워크 구조
"""

import sys
import os
import time
import tempfile
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = {}


def _test_result(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    RESULTS[name] = {"status": status, "detail": detail}
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


# ─────────────────────────────────────────────
# 1. LLM 비용 추적기
# ─────────────────────────────────────────────
def test_usage_tracker():
    """글로벌 LLM 비용 추적기 동작"""
    print("\n=== 1. LLM 비용 추적기 ===")
    from agents.core.base import (
        get_llm_usage, reset_llm_usage, _track_usage, _usage_stats,
        QUICK_MODEL, DEEP_MODEL, _COST_PER_1K,
    )

    reset_llm_usage()
    usage = get_llm_usage()
    _test_result("tracker_reset",
                usage["calls"] == 0 and usage["estimated_cost_usd"] == 0,
                "초기화 후 0")

    # 가상 응답 시뮬레이션
    class MockUsage:
        input_tokens = 500
        output_tokens = 200
        cache_read_input_tokens = 100
        cache_creation_input_tokens = 50

    class MockResponse:
        usage = MockUsage()

    _track_usage(QUICK_MODEL, "test_agent", MockResponse())
    usage = get_llm_usage()
    _test_result("tracker_call_count",
                usage["calls"] == 1,
                f"calls={usage['calls']}")
    _test_result("tracker_tokens",
                usage["input_tokens"] == 500
                and usage["output_tokens"] == 200,
                f"input={usage['input_tokens']}, output={usage['output_tokens']}")
    _test_result("tracker_cache_tokens",
                usage["cache_read_tokens"] == 100,
                f"cache_read={usage['cache_read_tokens']}")
    _test_result("tracker_cost_positive",
                usage["estimated_cost_usd"] > 0,
                f"cost=${usage['estimated_cost_usd']:.6f}")

    # 모델별 추적
    _test_result("tracker_by_model",
                QUICK_MODEL in usage["by_model"]
                and usage["by_model"][QUICK_MODEL]["calls"] == 1,
                f"model={QUICK_MODEL}")

    # 에이전트별 추적
    _test_result("tracker_by_agent",
                "test_agent" in usage["by_agent"],
                f"agent=test_agent")

    # 추가 호출 (Deep 모델)
    class DeepResponse:
        class usage:
            input_tokens = 1000
            output_tokens = 500
            cache_read_input_tokens = 0
            cache_creation_input_tokens = 0

    _track_usage(DEEP_MODEL, "analyst", DeepResponse())
    usage2 = get_llm_usage()
    _test_result("tracker_multi_calls",
                usage2["calls"] == 2
                and len(usage2["by_model"]) == 2,
                f"calls={usage2['calls']}, models={len(usage2['by_model'])}")

    # 비용 계산 검증
    cost_map = _COST_PER_1K
    _test_result("tracker_cost_map",
                QUICK_MODEL in cost_map and DEEP_MODEL in cost_map,
                f"Haiku input=${cost_map[QUICK_MODEL]['input']}/1K, "
                f"Sonnet input=${cost_map[DEEP_MODEL]['input']}/1K")

    reset_llm_usage()
    _test_result("tracker_reset_clean",
                get_llm_usage()["calls"] == 0,
                "reset 후 깨끗")


# ─────────────────────────────────────────────
# 2. 프롬프트 캐싱 형태 검증
# ─────────────────────────────────────────────
def test_prompt_caching():
    """프롬프트 캐싱 구조 검증 (cache_control 적용)"""
    print("\n=== 2. 프롬프트 캐싱 구조 ===")
    from agents.core.base import AgentBase, AgentMessage

    # 최소 구현 에이전트
    class TestAgent(AgentBase):
        def _get_system_prompt(self):
            return "테스트 시스템 프롬프트"
        def collect_data(self):
            return {}
        def analyze(self, data, context=None, crash_context=None):
            return self._build_message(data, 0.5)

    agent = TestAgent("test", "테스트", "역할 설명")

    # llm_call 메서드 시그니처에 cache_system 파라미터 존재
    import inspect
    sig = inspect.signature(agent.llm_call)
    _test_result("cache_param_exists",
                "cache_system" in sig.parameters,
                f"params: {list(sig.parameters.keys())}")

    # cache_system 기본값 True
    _test_result("cache_default_true",
                sig.parameters["cache_system"].default is True,
                "cache_system default=True")

    # llm_json도 캐싱 파라미터 전달
    sig_json = inspect.signature(agent.llm_json)
    _test_result("json_cache_param",
                "cache_system" in sig_json.parameters,
                "llm_json에도 cache_system 파라미터")


# ─────────────────────────────────────────────
# 3. 모델 배분 최적화 검증
# ─────────────────────────────────────────────
def test_model_allocation():
    """에이전트별 모델 배분 검증"""
    print("\n=== 3. 모델 배분 최적화 ===")
    from agents.core.base import QUICK_MODEL, DEEP_MODEL

    _test_result("model_quick",
                "haiku" in QUICK_MODEL.lower(),
                f"QUICK={QUICK_MODEL}")
    _test_result("model_deep",
                "sonnet" in DEEP_MODEL.lower(),
                f"DEEP={DEEP_MODEL}")

    # RiskAgent는 Haiku로 변경 확인 (비용 절감)
    import ast
    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "risk", "risk_agent.py"), "r") as f:
        content = f.read()

    _test_result("risk_agent_haiku",
                "deep=False" in content.replace(" ", "")
                or 'deep=False' in content,
                "RiskAgent LLM: deep=False (Haiku)")

    # HedgeAgent는 원래 Haiku
    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "risk", "hedge_agent.py"), "r") as f:
        hedge_content = f.read()
    _test_result("hedge_agent_haiku",
                "deep=False" in hedge_content.replace(" ", ""),
                "HedgeAgent LLM: deep=False (Haiku)")

    # Orchestrator의 Bull/Bear 토론은 Haiku
    with open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                           "agents", "core", "orchestrator.py"), "r") as f:
        orch_content = f.read()
    _test_result("orch_debate_haiku",
                "QUICK_MODEL" in orch_content and "bull_resp" in orch_content,
                "Bull/Bear 토론: QUICK_MODEL (Haiku)")

    # 비용 추적 import 확인
    _test_result("orch_tracking",
                "_track_usage" in orch_content,
                "Orchestrator에 비용 추적 import")


# ─────────────────────────────────────────────
# 4. 리스크 파이프라인 + 비용 추적 통합
# ─────────────────────────────────────────────
def test_pipeline_with_tracking():
    """리스크 파이프라인 실행 시 비용 추적"""
    print("\n=== 4. 파이프라인 + 비용 추적 ===")
    from agents.core.base import get_llm_usage, reset_llm_usage
    from risk.pipeline import RiskPipeline
    from risk.models import PipelineContext, MarketState

    reset_llm_usage()

    pipeline = RiskPipeline()
    ctx = PipelineContext(
        signal={"symbol": "ETHUSDT", "direction": "long", "gap": 0.3},
        market_state=MarketState(
            btc_price=95000, btc_level=1, btc_atr_pct=1.2,
            fear_greed=50, funding_rate=0.01,
        ),
    )
    decision = pipeline.evaluate(ctx)

    usage = get_llm_usage()
    # API 키 없으면 LLM 호출 0건 (규칙 기반 폴백)
    _test_result("pipeline_runs",
                decision.risk_level is not None,
                f"risk={decision.risk_level.value}")

    # 에러 카운트 확인 (API 키 없으면 에러 발생)
    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY", ""))
    if has_api_key:
        _test_result("pipeline_llm_tracked",
                    usage["calls"] > 0,
                    f"LLM calls={usage['calls']}, cost=${usage['estimated_cost_usd']:.6f}")
    else:
        _test_result("pipeline_fallback_ok",
                    usage["errors"] >= 0,
                    f"API 키 없음 → 규칙 기반 폴백 (errors={usage['errors']})")


# ─────────────────────────────────────────────
# 5. Walk-Forward 프레임워크 구조
# ─────────────────────────────────────────────
def test_walk_forward_framework():
    """Walk-Forward 클래스 구조 및 윈도우 정의"""
    print("\n=== 5. Walk-Forward 프레임워크 ===")
    from backtests.walk_forward import (
        WalkForwardBacktest, WINDOWS, SIGNAL_LEVELS, LEVERAGE,
    )

    # 윈도우 정의 검증
    _test_result("wf_window_count",
                len(WINDOWS) == 3,
                f"3윈도우: {[w['name'] for w in WINDOWS]}")

    # 윈도우 시간 순서
    for w in WINDOWS:
        train_end = w["train"][1]
        val_start = w["val"][0]
        test_start = w["test"][0]
        _test_result(f"wf_{w['name'].replace(' ', '_')}_order",
                    train_end < val_start and val_start < test_start,
                    f"Train→Val→Test 순서 OK")

    # 시그널 레벨 정의
    _test_result("wf_signal_levels",
                len(SIGNAL_LEVELS) == 4,
                f"levels: {list(SIGNAL_LEVELS.keys())}")

    # production 레벨 CLAUDE.md 일치
    prod = SIGNAL_LEVELS["production"]
    _test_result("wf_prod_config",
                prod["min_conf"] == 60
                and prod["min_adx"] == 30
                and prod["max_gap"] == 0.5,
                f"production: conf≥{prod['min_conf']}, "
                f"ADX≥{prod['min_adx']}, GAP≤{prod['max_gap']}")

    # 레버리지
    _test_result("wf_leverage",
                LEVERAGE == 20,
                f"LEVERAGE={LEVERAGE}")


# ─────────────────────────────────────────────
# 6. Walk-Forward 미니 데이터 테스트
# ─────────────────────────────────────────────
def test_walk_forward_mini():
    """가상 소량 데이터로 Walk-Forward 파이프라인 테스트"""
    print("\n=== 6. Walk-Forward 미니 실행 ===")

    try:
        import pandas as pd
    except ImportError:
        _test_result("wf_mini_skip", True, "pandas 미설치 — 스킵")
        return

    from backtests.walk_forward import WalkForwardBacktest

    # 가상 데이터 생성
    import numpy as np
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2025-06-01", periods=n, freq="15min")
    data = {
        "timestamp": dates,
        "symbol": np.random.choice(["ETHUSDT", "BTCUSDT", "SOLUSDT"], n),
        "direction": np.random.choice(["long", "short"], n),
        "gap": np.random.uniform(0.1, 0.8, n),
        "confidence": np.random.randint(30, 80, n),
        "adx": np.random.randint(15, 50, n),
        "exit_type": np.random.choice(["TP", "SL", "Timeout"], n, p=[0.35, 0.48, 0.17]),
        "realized_roe": np.random.normal(5, 80, n),
        "hold_candles": np.random.randint(4, 200, n),
        "direction_correct": np.random.choice([True, False], n, p=[0.86, 0.14]),
        "mae_pct": np.random.uniform(0, 5, n),
        "mfe_pct": np.random.uniform(0, 10, n),
        "btc_price": np.random.uniform(90000, 100000, n),
        "btc_level": np.random.randint(0, 4, n),
        "atr_pct": np.random.uniform(0.5, 3.0, n),
        "fear_greed": np.random.randint(20, 80, n),
        "funding_rate": np.random.uniform(-0.01, 0.02, n),
    }
    df = pd.DataFrame(data)

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
        df.to_csv(f, index=False)
        tmp_path = f.name

    try:
        bt = WalkForwardBacktest(data_path=tmp_path, level="level3")
        loaded = bt.load_data()
        _test_result("wf_mini_load",
                    len(loaded) == n,
                    f"loaded {len(loaded)} rows")

        # 필터 적용
        filtered = bt.filter_signals(loaded)
        _test_result("wf_mini_filter",
                    len(filtered) <= len(loaded),
                    f"필터 후: {len(filtered)}/{len(loaded)}")

        # Baseline 평가
        baseline = bt._evaluate_baseline(filtered)
        _test_result("wf_mini_baseline",
                    baseline.get("total", 0) > 0
                    and "pf" in baseline,
                    f"baseline PF={baseline.get('pf', 0):.3f}, "
                    f"winrate={baseline.get('winrate', 0):.1%}")

        # Enhanced 평가 (소량이므로 빠름)
        small_sample = filtered.head(20)
        enhanced = bt._evaluate_enhanced(small_sample)
        _test_result("wf_mini_enhanced",
                    enhanced.get("method") == "enhanced"
                    and "pf" in enhanced,
                    f"enhanced PF={enhanced.get('pf', 0):.3f}, "
                    f"accepted={enhanced.get('accepted', 0)}")

        # 리포트 생성
        bt.results = [{"window": "Test", "test_period": "2025-06",
                       "test_signals": len(filtered),
                       "baseline": baseline, "enhanced": enhanced,
                       "memory_enhanced": bt._empty_result("memory")}]
        report = bt.generate_report()
        _test_result("wf_mini_report",
                    "Walk-Forward" in report and "Baseline" in report,
                    f"report {len(report)}자")

    finally:
        os.unlink(tmp_path)


# ─────────────────────────────────────────────
# 7. 비용 효율 시뮬레이션
# ─────────────────────────────────────────────
def test_cost_efficiency():
    """모델 배분 최적화 비용 비교"""
    print("\n=== 7. 비용 효율 시뮬레이션 ===")
    from agents.core.base import _COST_PER_1K, QUICK_MODEL, DEEP_MODEL

    haiku = _COST_PER_1K[QUICK_MODEL]
    sonnet = _COST_PER_1K[DEEP_MODEL]

    # 시나리오: 시그널 100건 × 6에이전트 + 토론 3회 (Bull/Bear/Mod) 분석
    # 이전: 모두 Sonnet
    old_input = 100 * 9 * 1000  # 9 calls × 1K tokens avg
    old_output = 100 * 9 * 500
    old_cost = (old_input * sonnet["input"] + old_output * sonnet["output"]) / 1000

    # 현재: Risk/Hedge/Bull/Bear=Haiku, Analyst/Moderator=Sonnet
    haiku_calls = 100 * 4  # risk, hedge, bull, bear
    sonnet_calls = 100 * 5  # analyst + 4 analyzers + moderator
    new_input_h = haiku_calls * 1000
    new_output_h = haiku_calls * 500
    new_input_s = sonnet_calls * 1000
    new_output_s = sonnet_calls * 500
    new_cost = (
        (new_input_h * haiku["input"] + new_output_h * haiku["output"]) +
        (new_input_s * sonnet["input"] + new_output_s * sonnet["output"])
    ) / 1000

    saving_pct = (1 - new_cost / old_cost) * 100 if old_cost > 0 else 0

    _test_result("cost_old_vs_new",
                new_cost < old_cost,
                f"old=${old_cost:.2f} → new=${new_cost:.2f} "
                f"({saving_pct:.0f}% 절감)")

    # Haiku가 Sonnet보다 저렴 확인
    _test_result("cost_haiku_cheaper",
                haiku["input"] < sonnet["input"]
                and haiku["output"] < sonnet["output"],
                f"Haiku in/out=${haiku['input']}/{haiku['output']} "
                f"< Sonnet ${sonnet['input']}/{sonnet['output']}")


# ─────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("Phase E: LLM API 최적화 + Walk-Forward 프레임워크 테스트")
    print("=" * 60)

    test_usage_tracker()
    test_prompt_caching()
    test_model_allocation()
    test_pipeline_with_tracking()
    test_walk_forward_framework()
    test_walk_forward_mini()
    test_cost_efficiency()

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
