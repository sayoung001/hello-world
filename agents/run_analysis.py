"""
run_analysis.py — 멀티 에이전트 분석 실행 엔트리포인트
=====================================================
사용법:
  python -m agents.run_analysis                      # 시장 분석만 (포지션 없이)
  python -m agents.run_analysis --with-positions     # 포지션 포함 분석
  python -m agents.run_analysis --mvp                # MVP 모드 (Agent 1 + 5만)

v9 연동 시:
  from agents.run_analysis import run_market_analysis
  consensus = run_market_analysis(positions=open_positions)
"""

from __future__ import annotations
import os
import sys
import argparse

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.core.orchestrator import Orchestrator
from agents.core.protocol import PositionInfo
from agents.analyzers.btc_structure import BTCStructureAgent
from agents.analyzers.macro import MacroAgent
from agents.analyzers.correlation import CorrelationAgent
from agents.analyzers.alt_ecosystem import AltEcosystemAgent
from agents.analyzers.position_judge import PositionJudgeAgent
from agents.output.telegram_formatter import TelegramFormatter
from agents.memory.store import AnalysisMemory
from agents.shadow_logger import ShadowLogger


def build_orchestrator(mvp: bool = False, cg_client=None) -> Orchestrator:
    """오케스트레이터 구성"""
    orch = Orchestrator()

    # Agent 1: BTC 구조 분석가 (항상 포함)
    orch.register_context_agent(BTCStructureAgent(cg_client=cg_client))

    if not mvp:
        # Agent 2~4: 전체 모드
        orch.register_context_agent(MacroAgent(cg_client=cg_client))
        orch.register_context_agent(CorrelationAgent())
        orch.register_context_agent(AltEcosystemAgent())

    # Agent 5: 포지션 심판
    orch.register_position_agent(PositionJudgeAgent())

    return orch


def run_market_analysis(
    positions: list[PositionInfo] | None = None,
    mvp: bool = True,
    cg_client=None,
    telegram_token: str = "",
    telegram_chat_id: str = "",
) -> dict:
    """
    멀티 에이전트 시장 분석 실행

    :param positions: 현재 오픈 포지션 리스트
    :param mvp: True면 Agent 1+5만 사용
    :param cg_client: CoinGlass 클라이언트 (있으면 재사용)
    :param telegram_token: 텔레그램 봇 토큰
    :param telegram_chat_id: 텔레그램 채팅 ID
    :returns: 분석 결과 dict
    """
    # CoinGlass 클라이언트 (환경변수에서)
    if cg_client is None:
        cg_key = os.environ.get("COINGLASS_API_KEY", "")
        if cg_key:
            try:
                from coinglass_client import CoinGlassClient
                cg_client = CoinGlassClient(cg_key)
            except ImportError:
                pass

    # 오케스트레이터 구성 + 실행
    orch = build_orchestrator(mvp=mvp, cg_client=cg_client)
    consensus = orch.run(positions=positions)

    # 메모리 저장
    memory = AnalysisMemory()
    memory.store(consensus)

    # Shadow Mode 로깅
    shadow = ShadowLogger()
    shadow.log_analysis(consensus, trigger="manual")

    # 텔레그램 전송
    tg_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = telegram_chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    formatter = TelegramFormatter(tg_token, tg_chat)
    formatter.send(consensus)

    return {
        "overall_risk": consensus.overall_risk.value,
        "market_regime": consensus.market_regime.value,
        "btc_bias": consensus.btc_bias,
        "confidence": consensus.confidence,
        "warnings": consensus.warnings,
        "verdicts": consensus.position_verdicts,
        "risk_trend": memory.get_risk_trend(),
        "accuracy_report": shadow.generate_accuracy_report(),
    }


def main():
    parser = argparse.ArgumentParser(description="멀티 에이전트 시장 분석")
    parser.add_argument("--mvp", action="store_true", default=True,
                        help="MVP 모드 (Agent 1 + 5만)")
    parser.add_argument("--full", action="store_true",
                        help="전체 에이전트 모드 (Agent 1~5)")
    parser.add_argument("--with-positions", action="store_true",
                        help="포지션 포함 분석")
    args = parser.parse_args()

    mvp = not args.full

    # 예시 포지션 (--with-positions 사용 시)
    positions = None
    if args.with_positions:
        positions = [
            PositionInfo(symbol="SUI/USDT", direction="long",
                         entry_price=1.85, current_price=1.92),
            PositionInfo(symbol="WLD/USDT", direction="long",
                         entry_price=2.45, current_price=2.41),
        ]

    result = run_market_analysis(positions=positions, mvp=mvp)
    print(f"\n📋 분석 결과 요약:")
    print(f"  리스크: {result['overall_risk']}")
    print(f"  시장: {result['market_regime']}")
    print(f"  BTC: {result['btc_bias']}")

    # 정확도 리포트
    report = result.get("accuracy_report", {})
    if report.get("evaluated", 0) > 0:
        print(f"\n📊 Shadow Mode 정확도:")
        print(f"  평가 건수: {report['evaluated']}")
        print(f"  정확도: {report['accuracy_pct']}%")
        print(f"  FP율(과도경고): {report['false_positive_rate']}%")
        print(f"  FN율(미감지): {report['false_negative_rate']}%")


if __name__ == "__main__":
    main()
