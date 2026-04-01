"""
v9_hook.py — auto_trader_v9.py 연동 훅
========================================
기존 v9 봇에 최소한의 수정으로 에이전트 시스템을 연결.

사용법 (auto_trader_v9.py에 추가):
    # 상단 import
    from agents.v9_hook import AgentHook

    # __init__ 내부
    self.agent_hook = AgentHook(self.cg_client, self.btc_trend, self.tg)

    # try_enter() 에서 진입 직후 (self.positions.append(pos) 이후)
    self.agent_hook.on_position_open(pos, self.positions)

    # manage_positions() 에서 주기적 호출 (4시간마다)
    self.agent_hook.periodic_check(self.positions)

    # _close() 에서 포지션 청산 직후
    self.agent_hook.on_position_close(pos, close_reason, pnl_pct)

Shadow Mode: 실제 개입 없이 분석 결과만 텔레그램 전송.
"""

from __future__ import annotations
import os
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Any

from agents.core.orchestrator import Orchestrator
from agents.core.protocol import PositionInfo, MarketConsensus, RiskLevel
from agents.analyzers.btc_structure import BTCStructureAgent
from agents.analyzers.position_judge import PositionJudgeAgent
from agents.output.telegram_formatter import TelegramFormatter
from agents.memory.store import AnalysisMemory
from agents.shadow_logger import ShadowLogger

KST = timezone(timedelta(hours=9))


class AgentHook:
    """
    v9 봇에 연결하는 에이전트 훅

    기능:
    1. 포지션 오픈 시 자동 분석 트리거
    2. 4시간 주기 정기 분석
    3. 포지션 청산 시 실제 결과 기록 (Shadow Mode 정확도 측정)
    4. Shadow Mode (분석 결과만 전송, 개입 없음)

    Parameters:
        cg_client: CoinGlassClient 인스턴스
        btc_filter: BTCFilter 인스턴스 (btc_filter.py)
        tg: 텔레그램 인스턴스 (send 메서드)
        shadow_mode: True면 분석만, False면 액션 권고도 반환
        analysis_interval_h: 정기 분석 주기 (시간)
    """

    def __init__(self, cg_client=None, btc_filter=None, tg=None,
                 shadow_mode: bool = True, analysis_interval_h: int = 4):
        self.cg_client = cg_client
        self.btc_filter_instance = btc_filter
        self.tg = tg
        self.shadow_mode = shadow_mode
        self.analysis_interval = analysis_interval_h * 3600
        self._last_analysis_time = 0
        self._memory = AnalysisMemory()
        self._shadow = ShadowLogger()
        self._formatter = TelegramFormatter(
            os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            os.environ.get("TELEGRAM_CHAT_ID", "")
        )
        self._orch: Orchestrator | None = None
        # 포지션별 마지막 분석 타임스탬프 (outcome 매칭용)
        self._position_analysis_ts: dict[str, str] = {}

    def _get_orchestrator(self) -> Orchestrator:
        """오케스트레이터 지연 초기화"""
        if self._orch is None:
            self._orch = Orchestrator()
            self._orch.register_context_agent(
                BTCStructureAgent(
                    cg_client=self.cg_client,
                    btc_filter=self.btc_filter_instance,
                )
            )
            self._orch.register_position_agent(PositionJudgeAgent())
        return self._orch

    def _convert_positions(self, v9_positions: list) -> list[PositionInfo]:
        """v9 Position 객체 → PositionInfo 변환"""
        result = []
        for pos in v9_positions:
            try:
                current = getattr(pos, "current_price", 0) or getattr(pos, "last_price", 0)
                entry = pos.entry_price

                if current and entry:
                    if pos.direction == "long":
                        pnl_pct = (current - entry) / entry * 100
                    else:
                        pnl_pct = (entry - current) / entry * 100
                    roe_pct = pnl_pct * 20
                else:
                    pnl_pct = 0.0
                    roe_pct = 0.0

                hold_candles = 0
                if hasattr(pos, "hold_candles_15m"):
                    hold_candles = pos.hold_candles_15m()
                elif hasattr(pos, "entry_time"):
                    try:
                        entry_dt = datetime.fromisoformat(pos.entry_time)
                        now = datetime.now(KST)
                        if entry_dt.tzinfo is None:
                            entry_dt = entry_dt.replace(tzinfo=KST)
                        hold_candles = int((now - entry_dt).total_seconds() / 900)
                    except Exception:
                        pass

                result.append(PositionInfo(
                    symbol=pos.symbol,
                    direction=pos.direction,
                    entry_price=entry,
                    current_price=current or entry,
                    size=getattr(pos, "quantity", 0) * entry,
                    leverage=20,
                    pnl_pct=pnl_pct,
                    roe_pct=roe_pct,
                    hold_candles=hold_candles,
                ))
            except Exception as e:
                print(f"  ⚠️ 포지션 변환 실패 {getattr(pos, 'symbol', '?')}: {e}")
        return result

    def on_position_open(self, new_position, all_positions: list):
        """
        포지션 오픈 시 트리거 — 비동기로 분석 실행

        v9의 try_enter()에서 호출:
            self.agent_hook.on_position_open(pos, self.positions)
        """
        def _analyze():
            try:
                positions = self._convert_positions(all_positions)
                orch = self._get_orchestrator()
                consensus = orch.run(positions=positions)
                self._memory.store(consensus)
                self._shadow.log_analysis(consensus, trigger="진입")
                # 포지션별 타임스탬프 기록
                sym = getattr(new_position, "symbol", "")
                self._position_analysis_ts[sym] = consensus.timestamp
                self._send_result(consensus, trigger="진입")
            except Exception as e:
                print(f"  ⚠️ 에이전트 분석 실패: {e}")

        thread = threading.Thread(target=_analyze, daemon=True)
        thread.start()

    def periodic_check(self, positions: list):
        """
        주기적 분석 (4시간마다)

        v9의 manage_positions()에서 호출:
            self.agent_hook.periodic_check(self.positions)
        """
        now = time.time()
        if now - self._last_analysis_time < self.analysis_interval:
            return

        self._last_analysis_time = now

        if not positions:
            return

        def _analyze():
            try:
                pos_infos = self._convert_positions(positions)
                orch = self._get_orchestrator()
                consensus = orch.run(positions=pos_infos)
                self._memory.store(consensus)
                self._shadow.log_analysis(consensus, trigger="정기분석")
                # 모든 포지션의 타임스탬프 갱신
                for pos in positions:
                    sym = getattr(pos, "symbol", "")
                    if sym:
                        self._position_analysis_ts[sym] = consensus.timestamp
                self._send_result(consensus, trigger="정기분석")
            except Exception as e:
                print(f"  ⚠️ 정기 에이전트 분석 실패: {e}")

        thread = threading.Thread(target=_analyze, daemon=True)
        thread.start()

    def on_position_close(self, position, close_reason: str, pnl_pct: float):
        """
        포지션 청산 시 — 실제 결과를 Shadow Logger에 기록

        v9의 _close()에서 호출:
            self.agent_hook.on_position_close(pos, reason, roe_spot * 100)

        :param position: 청산된 포지션 객체
        :param close_reason: "TP", "SL", "SL_본전", "EE_EXIT", "TRAIL" 등
        :param pnl_pct: 실현 수익률 (%)
        """
        sym = getattr(position, "symbol", "")
        ts = self._position_analysis_ts.pop(sym, "")

        # close_reason → outcome 매핑
        if "TP" in close_reason or "TRAIL" in close_reason:
            outcome = "TP"
        elif "SL" in close_reason:
            outcome = "SL"
        elif "EE" in close_reason:
            outcome = "TIMEOUT"
        else:
            outcome = close_reason

        if ts:
            self._shadow.update_outcome(ts, outcome, pnl_pct)
            print(f"  [Shadow] {sym} 결과 기록: {outcome} ({pnl_pct:+.2f}%)")

    def get_accuracy_report(self) -> dict:
        """Shadow Mode 정확도 리포트 조회"""
        return self._shadow.generate_accuracy_report()

    def _send_result(self, consensus: MarketConsensus, trigger: str = ""):
        """분석 결과 전송 (텔레그램 또는 콘솔)"""
        prefix = f"[🤖 Agent {trigger}] "

        if self.tg:
            messages = self._formatter.format_consensus(consensus)
            for msg in messages:
                try:
                    self.tg.send(prefix + msg[:3900])
                except Exception:
                    pass

        # 위험 시 추가 경고
        if consensus.overall_risk == RiskLevel.DANGER:
            danger_msg = (f"🔴 DANGER — 에이전트 종합 판단: 위험\n"
                          f"경고: {', '.join(consensus.warnings[:3])}")
            if self.tg:
                try:
                    self.tg.send(danger_msg)
                except Exception:
                    pass
            print(f"  {danger_msg}")

        # Shadow Mode 로그
        if self.shadow_mode:
            trend = self._memory.get_risk_trend()
            print(f"  [Shadow] 리스크: {consensus.overall_risk.value} | "
                  f"추세: {trend} | 포지션: {len(consensus.position_verdicts)}개")

    def get_action_for_position(self, symbol: str) -> dict | None:
        """
        특정 포지션에 대한 최신 권고 조회.
        Shadow Mode가 아닐 때 v9에서 참조 가능.
        """
        if not self._orch or not self._orch._last_consensus:
            return None
        for v in self._orch._last_consensus.position_verdicts:
            if isinstance(v, dict) and v.get("symbol", "").replace("/", "") in symbol.replace("/", ""):
                return v
        return None
