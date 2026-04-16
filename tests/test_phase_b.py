"""
Phase B 모듈 동작 테스트
=========================
리스크 관리 에이전트 파이프라인:
models, config, analyst_agent, risk_agent, hedge_agent, leverage_agent, pipeline
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RESULTS = {}


def test_result(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    RESULTS[name] = {"status": status, "detail": detail}
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


def test_models():
    """1. Pydantic 데이터 모델 테스트"""
    print("\n=== 1. Pydantic 데이터 모델 ===")
    try:
        from risk.models import (
            RiskLevel, HedgeAction, Urgency,
            PositionContext, MarketState, PipelineContext,
            MarketAnalysis, RiskAssessment, PositionRiskAssessment,
            HedgeRecommendation, LeverageRecommendation, RiskDecision,
        )

        # Enum 테스트
        test_result("models_enum_risk", RiskLevel.CRITICAL.value == "critical",
                    f"RiskLevel.CRITICAL = {RiskLevel.CRITICAL.value}")
        test_result("models_enum_hedge", HedgeAction.PARTIAL_CLOSE.value == "partial_close",
                    f"HedgeAction.PARTIAL_CLOSE = {HedgeAction.PARTIAL_CLOSE.value}")

        # PositionContext 생성
        pos = PositionContext(
            symbol="ETHUSDT", direction="long", entry_price=3000.0,
            current_price=3100.0, size_usdt=500.0, leverage=20,
            pnl_pct=3.33, roe_pct=66.6, hold_candles=24,
            stop_loss=2900.0, take_profit=3300.0,
        )
        test_result("models_position", pos.symbol == "ETHUSDT" and pos.leverage == 20,
                    f"PositionContext: {pos.symbol} {pos.direction} lev={pos.leverage}")

        # MarketState 생성
        ms = MarketState(
            btc_price=95000, btc_change_1h=-0.5, btc_change_4h=1.2,
            btc_change_24h=-2.3, btc_level=2, btc_rsi=45.0,
            btc_atr_pct=1.8, fear_greed=35, funding_rate=0.01,
        )
        test_result("models_market", ms.btc_price == 95000 and ms.btc_level == 2,
                    f"MarketState: BTC ${ms.btc_price:,.0f} level={ms.btc_level}")

        # PipelineContext 구성
        ctx = PipelineContext(
            positions=[pos], market_state=ms,
            account_balance=1000.0, total_exposure=500.0,
        )
        test_result("models_context", len(ctx.positions) == 1 and ctx.account_balance == 1000,
                    f"PipelineContext: {len(ctx.positions)} pos, bal={ctx.account_balance}")

        # RiskDecision 전체 구조
        rd = RiskDecision(
            risk_level=RiskLevel.MEDIUM,
            urgency=Urgency.LOW,
            reasoning="테스트",
        )
        test_result("models_decision", rd.risk_level == RiskLevel.MEDIUM,
                    f"RiskDecision: {rd.risk_level.value}")

        # JSON 직렬화/역직렬화
        rd_json = rd.model_dump()
        rd_back = RiskDecision(**rd_json)
        test_result("models_serialization",
                    rd_back.risk_level == RiskLevel.MEDIUM,
                    f"JSON roundtrip OK, keys={len(rd_json)}")

    except Exception as e:
        test_result("models", False, str(e))


def test_config():
    """2. 리스크 설정 테스트"""
    print("\n=== 2. 리스크 설정 ===")
    try:
        from risk import config

        test_result("config_leverage_range",
                    config.LEVERAGE_MIN == 10 and config.LEVERAGE_MAX == 20,
                    f"레버리지 범위: {config.LEVERAGE_MIN}~{config.LEVERAGE_MAX}")

        test_result("config_atr_map",
                    len(config.LEVERAGE_ATR_MAP) == 4,
                    f"ATR 매핑: {config.LEVERAGE_ATR_MAP}")

        test_result("config_risk_thresholds",
                    config.RISK_SCORE_THRESHOLDS["critical"] == 85,
                    f"임계값: {config.RISK_SCORE_THRESHOLDS}")

        test_result("config_hedge_max",
                    config.HEDGE_MAX_SIZE_PCT == 50.0,
                    f"최대 헷지: {config.HEDGE_MAX_SIZE_PCT}%")

    except Exception as e:
        test_result("config", False, str(e))


def test_analyst_agent():
    """3. Analyst Agent 테스트 (규칙 기반)"""
    print("\n=== 3. Analyst Agent (규칙 기반) ===")
    try:
        from risk.analyst_agent import AnalystAgent
        from risk.models import PipelineContext, MarketState, PositionContext

        agent = AnalystAgent()
        test_result("analyst_init",
                    agent.agent_id == "analyst" and agent.agent_name == "시장 분석가",
                    f"id={agent.agent_id}, name={agent.agent_name}")

        # AgentBase 상속 확인
        from agents.core.base import AgentBase
        test_result("analyst_inheritance",
                    isinstance(agent, AgentBase),
                    "AgentBase 상속 확인")

        # 정상 시장 분석
        ctx_normal = PipelineContext(
            market_state=MarketState(
                btc_price=95000, btc_change_4h=0.5, btc_change_24h=1.2,
                btc_level=1, btc_rsi=55, btc_atr_pct=1.2,
                fear_greed=50, funding_rate=0.01, dominant_trend="neutral",
            ),
        )
        result_normal = agent.analyze_market(ctx_normal)
        test_result("analyst_normal",
                    result_normal.volatility_regime == "normal",
                    f"정상 시장: sentiment={result_normal.overall_sentiment}, "
                    f"vol={result_normal.volatility_regime}, risks={len(result_normal.key_risks)}")

        # 고위험 시장 분석
        ctx_danger = PipelineContext(
            market_state=MarketState(
                btc_price=85000, btc_change_4h=-3.0, btc_change_24h=-8.0,
                btc_level=5, btc_rsi=22, btc_atr_pct=3.5,
                fear_greed=12, funding_rate=-0.08, dominant_trend="bearish",
            ),
            total_exposure=3000, account_balance=500,
        )
        result_danger = agent.analyze_market(ctx_danger)
        test_result("analyst_danger_risks",
                    len(result_danger.key_risks) >= 3,
                    f"고위험: risks={result_danger.key_risks}")
        test_result("analyst_danger_sentiment",
                    result_danger.overall_sentiment == "bearish",
                    f"센티먼트: {result_danger.overall_sentiment} "
                    f"(score={result_danger.sentiment_score})")
        test_result("analyst_danger_volatility",
                    result_danger.volatility_regime == "extreme",
                    f"변동성: {result_danger.volatility_regime}")

        # Vision/RAG 반영
        ctx_vision = PipelineContext(
            market_state=MarketState(btc_level=2, btc_atr_pct=1.5),
            vision_features={"vision_risk_level": 0.9},
            rag_context={"historical_sl_hit_rate": 0.75},
        )
        result_vision = agent.analyze_market(ctx_vision)
        has_vision_risk = any("Vision" in r for r in result_vision.key_risks)
        has_rag_risk = any("SL" in r for r in result_vision.key_risks)
        test_result("analyst_vision_rag",
                    has_vision_risk and has_rag_risk,
                    f"Vision/RAG 리스크 반영: {result_vision.key_risks}")

    except Exception as e:
        test_result("analyst_agent", False, str(e))


def test_risk_agent():
    """4. Risk Agent 테스트 (규칙 기반)"""
    print("\n=== 4. Risk Agent (규칙 기반) ===")
    try:
        from risk.risk_agent import RiskAgent
        from risk.models import (
            PipelineContext, MarketState, PositionContext,
            MarketAnalysis, RiskLevel,
        )

        agent = RiskAgent()
        test_result("risk_init", agent.agent_id == "risk",
                    f"id={agent.agent_id}")

        from agents.core.base import AgentBase
        test_result("risk_inheritance", isinstance(agent, AgentBase),
                    "AgentBase 상속 확인")

        # 빈 포지션
        ctx_empty = PipelineContext()
        ma_empty = MarketAnalysis()
        result_empty = agent.assess(ctx_empty, ma_empty)
        test_result("risk_no_positions",
                    result_empty.portfolio_risk == RiskLevel.LOW,
                    f"빈 포지션: {result_empty.portfolio_risk.value}")

        # 정상 포지션
        pos_ok = PositionContext(
            symbol="ETHUSDT", direction="long", entry_price=3000,
            current_price=3100, leverage=20, pnl_pct=3.33, roe_pct=66.6,
            hold_candles=24, stop_loss=2900, take_profit=3300,
        )
        ctx_ok = PipelineContext(
            positions=[pos_ok],
            market_state=MarketState(btc_level=1, btc_atr_pct=1.0),
            account_balance=1000, total_exposure=500,
        )
        ma_ok = MarketAnalysis(overall_sentiment="neutral", volatility_regime="normal")
        result_ok = agent.assess(ctx_ok, ma_ok)
        test_result("risk_normal_position",
                    len(result_ok.position_assessments) == 1,
                    f"포지션 1개 평가, 리스크: {result_ok.portfolio_risk.value}, "
                    f"점수: {result_ok.total_risk_score}")

        # 고위험 포지션 (큰 손실, 청산 근접)
        pos_danger = PositionContext(
            symbol="SOLUSDT", direction="long", entry_price=150,
            current_price=144, leverage=20, pnl_pct=-4.0, roe_pct=-80.0,
            hold_candles=400, stop_loss=142, take_profit=165,
        )
        ctx_danger = PipelineContext(
            positions=[pos_danger],
            market_state=MarketState(btc_level=4, btc_atr_pct=2.5),
            account_balance=500, total_exposure=2000,
        )
        ma_danger = MarketAnalysis(
            overall_sentiment="bearish", volatility_regime="high",
            key_risks=["BTC 급락"],
        )
        result_danger = agent.assess(ctx_danger, ma_danger)
        test_result("risk_danger_position",
                    result_danger.total_risk_score >= 60,
                    f"고위험 점수: {result_danger.total_risk_score}, "
                    f"level: {result_danger.portfolio_risk.value}")

        pa = result_danger.position_assessments[0]
        test_result("risk_danger_action",
                    pa.action in ("close", "reduce", "tighten_sl"),
                    f"조치: {pa.action}, 리스크: {pa.risk_level.value}")

    except Exception as e:
        test_result("risk_agent", False, str(e))


def test_hedge_agent():
    """5. Hedge Agent 테스트 (규칙 기반)"""
    print("\n=== 5. Hedge Agent (규칙 기반) ===")
    try:
        from risk.hedge_agent import HedgeAgent
        from risk.models import (
            PipelineContext, PositionContext, MarketState,
            RiskAssessment, PositionRiskAssessment, RiskLevel,
            HedgeAction,
        )

        agent = HedgeAgent()
        test_result("hedge_init", agent.agent_id == "hedge",
                    f"id={agent.agent_id}")

        from agents.core.base import AgentBase
        test_result("hedge_inheritance", isinstance(agent, AgentBase),
                    "AgentBase 상속 확인")

        # 리스크 낮음 → 헷지 불필요
        ctx_low = PipelineContext(
            positions=[PositionContext(symbol="ETHUSDT", size_usdt=500)],
        )
        risk_low = RiskAssessment(
            portfolio_risk=RiskLevel.LOW, total_risk_score=25,
        )
        result_low = agent.recommend(ctx_low, risk_low)
        test_result("hedge_no_action",
                    result_low.action == HedgeAction.NONE,
                    f"저위험: action={result_low.action.value}, reason={result_low.reasoning[:60]}")

        # 고위험 → 부분 청산
        ctx_high = PipelineContext(
            positions=[PositionContext(symbol="ETHUSDT", size_usdt=500)],
        )
        risk_high = RiskAssessment(
            portfolio_risk=RiskLevel.HIGH,
            total_risk_score=75,
            position_assessments=[
                PositionRiskAssessment(
                    symbol="ETHUSDT", risk_level=RiskLevel.HIGH,
                    action="reduce", reasoning="ROE -60%, 청산 근접",
                ),
            ],
        )
        result_high = agent.recommend(ctx_high, risk_high)
        test_result("hedge_partial_close",
                    result_high.action in (HedgeAction.PARTIAL_CLOSE, HedgeAction.CORRELATED_HEDGE),
                    f"고위험: action={result_high.action.value}, size={result_high.size_pct}%")

        # CRITICAL 다수 → 전량 청산
        risk_crit = RiskAssessment(
            portfolio_risk=RiskLevel.CRITICAL,
            total_risk_score=92,
            position_assessments=[
                PositionRiskAssessment(symbol="ETHUSDT", risk_level=RiskLevel.CRITICAL),
                PositionRiskAssessment(symbol="BTCUSDT", risk_level=RiskLevel.CRITICAL),
            ],
        )
        ctx_crit = PipelineContext(
            positions=[
                PositionContext(symbol="ETHUSDT", size_usdt=500),
                PositionContext(symbol="BTCUSDT", size_usdt=800),
            ],
        )
        result_crit = agent.recommend(ctx_crit, risk_crit)
        test_result("hedge_full_exit",
                    result_crit.action == HedgeAction.FULL_EXIT,
                    f"CRITICAL: action={result_crit.action.value}")

        # 상관 코인 매핑 확인
        test_result("hedge_correlated_pairs",
                    "ETHUSDT" in agent.CORRELATED_PAIRS,
                    f"상관 매핑: {len(agent.CORRELATED_PAIRS)}쌍")

    except Exception as e:
        test_result("hedge_agent", False, str(e))


def test_leverage_agent():
    """6. Leverage Agent 테스트 (규칙 기반)"""
    print("\n=== 6. Leverage Agent (규칙 기반) ===")
    try:
        from risk.leverage_agent import LeverageAgent
        from risk.models import (
            PipelineContext, MarketState, PositionContext,
            RiskAssessment, RiskLevel,
        )

        agent = LeverageAgent()
        test_result("leverage_init", agent.agent_id == "leverage",
                    f"id={agent.agent_id}")

        from agents.core.base import AgentBase
        test_result("leverage_inheritance", isinstance(agent, AgentBase),
                    "AgentBase 상속 확인")

        # 저변동 → 20x 유지
        ctx_calm = PipelineContext(
            market_state=MarketState(btc_atr_pct=0.8, btc_level=1, fear_greed=50),
            positions=[PositionContext(leverage=20)],
        )
        risk_calm = RiskAssessment(portfolio_risk=RiskLevel.LOW)
        result_calm = agent.recommend(ctx_calm, risk_calm)
        test_result("leverage_calm",
                    result_calm.recommended_leverage == 20,
                    f"저변동: {result_calm.recommended_leverage}x")

        # 고변동 → 감소
        ctx_volatile = PipelineContext(
            market_state=MarketState(
                btc_atr_pct=2.5, btc_level=4, fear_greed=15,
                funding_rate=-0.08,
            ),
            positions=[PositionContext(leverage=20)],
            recent_losses=4,
            vision_features={"vision_risk_level": 0.85},
        )
        risk_volatile = RiskAssessment(portfolio_risk=RiskLevel.HIGH)
        result_volatile = agent.recommend(ctx_volatile, risk_volatile)
        test_result("leverage_volatile",
                    result_volatile.recommended_leverage < 15,
                    f"고변동: {result_volatile.recommended_leverage}x "
                    f"(감산: {result_volatile.risk_factors})")
        test_result("leverage_min_bound",
                    result_volatile.recommended_leverage >= 10,
                    f"최소 10x 보장: {result_volatile.recommended_leverage}x")

        # CRITICAL → 10x 강제
        risk_crit = RiskAssessment(portfolio_risk=RiskLevel.CRITICAL)
        ctx_crit = PipelineContext(
            market_state=MarketState(btc_atr_pct=1.5),
            positions=[PositionContext(leverage=20)],
        )
        result_crit = agent.recommend(ctx_crit, risk_crit)
        test_result("leverage_critical",
                    result_crit.recommended_leverage <= 10,
                    f"CRITICAL: {result_crit.recommended_leverage}x")

    except Exception as e:
        test_result("leverage_agent", False, str(e))


def test_pipeline():
    """7. 파이프라인 통합 테스트"""
    print("\n=== 7. 파이프라인 통합 테스트 ===")
    try:
        from risk.pipeline import RiskPipeline
        from risk.models import (
            PipelineContext, MarketState, PositionContext,
            RiskLevel, HedgeAction,
        )

        pipeline = RiskPipeline()
        test_result("pipeline_init",
                    pipeline.analyst is not None and pipeline.risk_agent is not None,
                    "4개 에이전트 초기화 완료")

        # 시나리오 1: 정상 시장, 이익 포지션
        ctx1 = PipelineContext(
            positions=[
                PositionContext(
                    symbol="ETHUSDT", direction="long", entry_price=3000,
                    current_price=3100, size_usdt=500, leverage=20,
                    pnl_pct=3.33, roe_pct=66.6, hold_candles=24,
                    stop_loss=2900, take_profit=3300,
                ),
            ],
            market_state=MarketState(
                btc_price=95000, btc_change_4h=0.5, btc_level=1,
                btc_rsi=55, btc_atr_pct=1.2, fear_greed=50,
            ),
            account_balance=1000, total_exposure=500,
        )
        decision1 = pipeline.evaluate(ctx1)
        test_result("pipeline_scenario1_risk",
                    decision1.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM),
                    f"정상 시장: risk={decision1.risk_level.value}")
        test_result("pipeline_scenario1_hedge",
                    decision1.hedge_recommendation.action == HedgeAction.NONE,
                    f"헷지: {decision1.hedge_recommendation.action.value}")
        test_result("pipeline_scenario1_leverage",
                    decision1.leverage_recommendation.recommended_leverage >= 15,
                    f"레버리지: {decision1.leverage_recommendation.recommended_leverage}x")

        # 시나리오 2: 위기 시장, 손실 포지션
        ctx2 = PipelineContext(
            positions=[
                PositionContext(
                    symbol="SOLUSDT", direction="long", entry_price=150,
                    current_price=143, size_usdt=800, leverage=20,
                    pnl_pct=-4.67, roe_pct=-93.3, hold_candles=200,
                    stop_loss=141, take_profit=165,
                ),
            ],
            market_state=MarketState(
                btc_price=82000, btc_change_4h=-4.0, btc_change_24h=-9.0,
                btc_level=5, btc_rsi=18, btc_atr_pct=3.8,
                fear_greed=10, funding_rate=-0.1, dominant_trend="bearish",
            ),
            account_balance=500, total_exposure=800,
            recent_losses=3,
        )
        decision2 = pipeline.evaluate(ctx2)
        test_result("pipeline_scenario2_risk",
                    decision2.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL),
                    f"위기 시장: risk={decision2.risk_level.value}")
        test_result("pipeline_scenario2_leverage",
                    decision2.leverage_recommendation.recommended_leverage <= 12,
                    f"레버리지 감소: {decision2.leverage_recommendation.recommended_leverage}x")

        # 시나리오 3: 빈 포지션
        ctx3 = PipelineContext(
            market_state=MarketState(btc_price=95000),
        )
        decision3 = pipeline.evaluate(ctx3)
        test_result("pipeline_scenario3_empty",
                    decision3.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM),
                    f"빈 포지션: risk={decision3.risk_level.value}")

        # 텔레그램 포맷
        tg_msg = RiskPipeline.format_telegram(decision2)
        test_result("pipeline_telegram",
                    "리스크 파이프라인" in tg_msg and len(tg_msg) > 50,
                    f"TG 메시지 길이: {len(tg_msg)}자")

        # 통계
        stats = pipeline.stats
        test_result("pipeline_stats",
                    stats["run_count"] == 3,
                    f"실행 횟수: {stats['run_count']}")

        # reasoning 검증
        test_result("pipeline_reasoning",
                    len(decision2.reasoning) > 10,
                    f"판단 근거: {decision2.reasoning[:80]}...")

        # timestamp 검증
        test_result("pipeline_timestamp",
                    "KST" in decision1.timestamp,
                    f"타임스탬프: {decision1.timestamp}")

    except Exception as e:
        import traceback
        test_result("pipeline", False, str(e) + "\n" + traceback.format_exc()[-300:])


def test_signal_evaluation():
    """8. 시그널 평가 래퍼 테스트"""
    print("\n=== 8. 시그널 평가 래퍼 ===")
    try:
        from risk.pipeline import RiskPipeline

        pipeline = RiskPipeline()
        decision = pipeline.evaluate_signal(
            signal={"symbol": "ETHUSDT", "direction": "long", "confidence": 70, "gap": 0.5},
            market_state={"btc_price": 95000, "btc_level": 2, "btc_atr_pct": 1.5},
        )
        test_result("signal_eval",
                    decision.risk_level is not None and decision.timestamp != "",
                    f"시그널 평가: risk={decision.risk_level.value}")

    except Exception as e:
        test_result("signal_eval", False, str(e))


def main():
    print("=" * 60)
    print("Phase B 모듈 동작 테스트 — 리스크 관리 에이전트")
    print("=" * 60)

    test_models()
    test_config()
    test_analyst_agent()
    test_risk_agent()
    test_hedge_agent()
    test_leverage_agent()
    test_pipeline()
    test_signal_evaluation()

    # 요약
    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)
    passed = sum(1 for v in RESULTS.values() if v["status"] == "PASS")
    failed = sum(1 for v in RESULTS.values() if v["status"] == "FAIL")
    print(f"총 {len(RESULTS)}건: PASS {passed}, FAIL {failed}")

    if failed > 0:
        print("\n실패 항목:")
        for name, v in RESULTS.items():
            if v["status"] == "FAIL":
                print(f"  - {name}: {v['detail']}")

    return RESULTS


if __name__ == "__main__":
    main()
