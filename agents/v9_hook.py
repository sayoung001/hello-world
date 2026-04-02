"""
v9_hook.py — auto_trader_v9.py 연동 훅
========================================
기존 v9 봇에 최소한의 수정으로 에이전트 시스템을 연결.

사용법 (auto_trader_v9.py에 추가):
    # 상단 import
    from agents.v9_hook import AgentHook

    # __init__ 내부
    self.agent_hook = AgentHook(self.cg_client, self.btc_trend, self.tg,
                                exchange=self.exchange)

    # 메인 루프 최상단 (매 15초)
    self.agent_hook.crash_check(self.positions)

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
from agents.analyzers.macro import MacroAgent
from agents.analyzers.correlation import CorrelationAgent
from agents.analyzers.alt_ecosystem import AltEcosystemAgent
from agents.analyzers.news_sentiment import NewsSentimentAgent
from agents.analyzers.position_judge import PositionJudgeAgent
from agents.output.telegram_formatter import TelegramFormatter
from agents.memory.store import AnalysisMemory
from agents.shadow_logger import ShadowLogger
from agents.crash_detector import CrashDetector
from agents.trend_reversal_detector import TrendReversalDetector

KST = timezone(timedelta(hours=9))

# 긴급분석 중복 방지 (동시에 여러 스레드 실행 차단)
_emergency_lock = threading.Lock()


class AgentHook:
    """
    v9 봇에 연결하는 에이전트 훅

    기능:
    1. 포지션 오픈 시 자동 분석 트리거
    2. 4시간 주기 정기 분석
    3. 포지션 청산 시 실제 결과 기록 (Shadow Mode 정확도 측정)
    4. Shadow Mode (분석 결과만 전송, 개입 없음)
    5. 실시간 급락 감지 → 전체 6에이전트 긴급 분석 → 종합 판단 텔레그램

    Parameters:
        cg_client: CoinGlassClient 인스턴스
        btc_filter: BTCFilter 인스턴스 (btc_filter.py)
        tg: 텔레그램 인스턴스 (send 메서드)
        exchange: ccxt Binance 인스턴스 (급락 감지용)
        shadow_mode: True면 분석만, False면 액션 권고도 반환
        analysis_interval_h: 정기 분석 주기 (시간)
    """

    def __init__(self, cg_client=None, btc_filter=None, tg=None,
                 exchange=None, shadow_mode: bool = True,
                 analysis_interval_h: int = 4):
        self.cg_client = cg_client
        self.btc_filter_instance = btc_filter
        self.tg = tg
        self.exchange = exchange
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
        self._emergency_orch: Orchestrator | None = None
        self._position_analysis_ts: dict[str, str] = {}
        # [V9.5] 실시간 급락 감지기
        self._crash_detector = CrashDetector(exchange=exchange)
        self._emergency_running = False
        # [V9.6] BTC 방향 전환 감지기
        self._trend_detector = TrendReversalDetector(exchange=exchange)

    # ── 오케스트레이터 초기화 ──

    def _get_orchestrator(self) -> Orchestrator:
        """정기분석용 오케스트레이터 (MVP: Agent 1 + 6 + 5)"""
        if self._orch is None:
            self._orch = Orchestrator()
            self._orch.register_context_agent(
                BTCStructureAgent(
                    cg_client=self.cg_client,
                    btc_filter=self.btc_filter_instance,
                )
            )
            self._orch.register_context_agent(NewsSentimentAgent())
            self._orch.register_position_agent(PositionJudgeAgent())
        return self._orch

    def _get_emergency_orchestrator(self) -> Orchestrator:
        """긴급분석용 오케스트레이터 (전체: Agent 1~6)"""
        # 매번 새로 생성 (긴급 상황에서 이전 상태 간섭 방지)
        orch = Orchestrator()
        # Agent 1: BTC 구조 (Factual)
        orch.register_context_agent(
            BTCStructureAgent(
                cg_client=self.cg_client,
                btc_filter=self.btc_filter_instance,
            )
        )
        # Agent 2: 매크로 (Subjective)
        orch.register_context_agent(
            MacroAgent(cg_client=self.cg_client, exchange=self.exchange)
        )
        # Agent 3: 상관관계 (Factual)
        orch.register_context_agent(CorrelationAgent(exchange=self.exchange))
        # Agent 4: 알트 생태계 (Subjective)
        orch.register_context_agent(AltEcosystemAgent(exchange=self.exchange))
        # Agent 6: 뉴스/이슈 (Subjective) — 급락 원인 파악 핵심
        orch.register_context_agent(NewsSentimentAgent())
        # Agent 5: 포지션 심판
        orch.register_position_agent(PositionJudgeAgent())
        return orch

    # ── 포지션 변환 ──

    def _convert_positions(self, v9_positions: list) -> list[PositionInfo]:
        """v9 Position 객체 → PositionInfo 변환"""
        result = []
        for pos in v9_positions:
            try:
                entry = pos.entry_price

                # 현재가: exchange에서 실시간 조회 (Position 객체에는 current_price 없음)
                current = 0.0
                try:
                    if self.exchange:
                        ticker = self.exchange.fetch_ticker(pos.symbol)
                        current = float(ticker.get("last", 0))
                except Exception:
                    pass
                if not current:
                    current = getattr(pos, "current_price", 0) or getattr(pos, "last_price", 0)

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

    # ── 오류 전송 ──

    def _send_error(self, context: str, error: Exception):
        """에이전트 오류를 텔레그램으로 전송"""
        err_msg = (
            f"⚠️ [Agent 오류] {context}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"에러: {type(error).__name__}: {str(error)[:200]}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"봇 운영에는 영향 없음 (Shadow Mode)"
        )
        print(f"  {err_msg}")
        if self.tg:
            try:
                self.tg.send(err_msg)
            except Exception:
                pass

    # ── 트리거 1: 포지션 오픈 ──

    def on_position_open(self, new_position, all_positions: list):
        """포지션 오픈 시 트리거 — 비동기 분석"""
        pos_snapshot = list(all_positions)  # 스레드 안전 복사

        def _analyze():
            try:
                positions = self._convert_positions(pos_snapshot)
                orch = self._get_orchestrator()
                consensus = orch.run(positions=positions)
                self._memory.store(consensus)
                self._shadow.log_analysis(consensus, trigger="진입")
                sym = getattr(new_position, "symbol", "")
                self._position_analysis_ts[sym] = consensus.timestamp
                self._send_result(consensus, trigger="진입")
            except Exception as e:
                self._send_error("진입 분석", e)

        threading.Thread(target=_analyze, daemon=True).start()

    # ── 트리거 2: 정기 분석 (4h) ──

    def periodic_check(self, positions: list | None = None):
        """주기적 분석 (4시간마다) — 포지션 없어도 시장 상황 분석"""
        now = time.time()
        if now - self._last_analysis_time < self.analysis_interval:
            return
        self._last_analysis_time = now

        pos_snapshot = list(positions) if positions else []

        def _analyze():
            try:
                pos_infos = self._convert_positions(pos_snapshot)
                orch = self._get_orchestrator()
                consensus = orch.run(positions=pos_infos)
                self._memory.store(consensus)
                self._shadow.log_analysis(consensus, trigger="정기분석")
                for pos in pos_snapshot:
                    sym = getattr(pos, "symbol", "")
                    if sym:
                        self._position_analysis_ts[sym] = consensus.timestamp
                self._send_result(consensus, trigger="정기분석")
            except Exception as e:
                self._send_error("정기분석 (4h)", e)

        threading.Thread(target=_analyze, daemon=True).start()

    # ── 트리거 3a: BTC 방향 전환 감지 ──

    def trend_check(self, positions: list | None = None):
        """
        BTC 방향 전환 감지 — v9 메인 루프에서 매 사이클(15초) 호출.
        포지션 방향과 반대로 BTC 추세가 전환되면 텔레그램 알림.
        """
        if not positions:
            return
        try:
            pos_snapshot = list(positions)
            alerts = self._trend_detector.check(pos_snapshot)
            for alert in alerts:
                msg = TrendReversalDetector.format_alert(alert)
                print(f"  [추세전환] {alert['symbol']} — BTC {alert['btc_trend']}")
                if self.tg:
                    self.tg.send(msg)
        except Exception as e:
            print(f"  ⚠️ 추세 감지 오류: {e}")

    # ── 트리거 3b: 급락 감지 → 긴급 6에이전트 분석 ──

    def crash_check(self, positions: list | None = None):
        """
        실시간 급락 감지 — v9 메인 루프에서 매 사이클(15초) 호출.

        감지 시:
        1. 즉시 "급락 감지, 분석 중" 속보 전송
        2. 전체 6에이전트 긴급 분석 비동기 트리거
        3. 분석 완료 후 종합 판단 (원인 + 홀드/청산) 전송
        """
        try:
            crash = self._crash_detector.check()
            if not crash:
                return

            level = crash.get("level", 0)

            # Level 1: 간단 알림만 (분석 트리거 안 함)
            if level == 1:
                self._send_brief_alert(crash)
                return

            # Level 2+: 속보 + 긴급 전체 에이전트 분석
            with _emergency_lock:
                if self._emergency_running:
                    return  # 이미 긴급 분석 진행 중
                self._emergency_running = True

            self._send_brief_alert(crash)
            # positions 스냅샷 복사 (스레드 안전 — 메인루프에서 변경될 수 있음)
            pos_snapshot = list(positions) if positions else []

            def _emergency():
                try:
                    self._run_emergency_analysis(crash, pos_snapshot)
                finally:
                    with _emergency_lock:
                        self._emergency_running = False

            threading.Thread(target=_emergency, daemon=True).start()

        except Exception:
            pass  # 급락 감지 실패가 봇을 멈추면 안 됨

    def _send_brief_alert(self, crash: dict):
        """급락 감지 즉시 속보 (1~2줄, 분석 전)"""
        level = crash["level"]
        icons = {1: "⚠️", 2: "🔴", 3: "🚨", 4: "💀"}
        icon = icons.get(level, "⚠️")

        msg = (
            f"{icon} [급락 감지] BTC {crash['window']} {crash['change_pct']:+.2f}% "
            f"(ROE {crash['roe_impact']:+.0f}%)"
        )
        if level >= 2:
            msg += "\n📡 전체 에이전트 긴급 분석 시작..."

        print(f"  {msg}")
        if self.tg:
            try:
                self.tg.send(msg)
            except Exception:
                pass

    def _run_emergency_analysis(self, crash: dict, v9_positions: list):
        """
        전체 6에이전트 긴급 분석 실행.

        기존 정기분석과 다른 점:
        - 전체 6에이전트 (MVP 아님)
        - 급락 컨텍스트가 Orchestrator에 전달됨
        - 포지션 유무와 무관하게 실행
        - 텔레그램 포맷이 긴급 모드
        """
        try:
            print(f"\n{'='*60}")
            print(f"🚨 긴급 분석 시작 (Level {crash['level']}: {crash['label']})")
            print(f"{'='*60}")

            orch = self._get_emergency_orchestrator()
            positions = self._convert_positions(v9_positions) if v9_positions else []

            # 급락 컨텍스트를 오케스트레이터에 전달 → 에이전트들이 원인 조사
            consensus = orch.run(positions=positions, crash_context=crash)
            self._memory.store(consensus)
            self._shadow.log_analysis(consensus, trigger=f"긴급L{crash['level']}")

            # 타임스탬프 갱신
            for pos in v9_positions:
                sym = getattr(pos, "symbol", "")
                if sym:
                    self._position_analysis_ts[sym] = consensus.timestamp

            # 긴급 포맷으로 텔레그램 전송
            self._send_emergency_result(crash, consensus)

            # 정기분석 타이머 리셋 (바로 직후 중복 분석 방지)
            self._last_analysis_time = time.time()

        except Exception as e:
            self._send_error(f"긴급분석 L{crash['level']}", e)

    def _send_emergency_result(self, crash: dict, consensus: MarketConsensus):
        """긴급 분석 결과 텔레그램 전송 — 원인 + 판단 + 포지션별 권고"""
        messages = self._formatter.format_emergency(crash, consensus)
        if self.tg:
            for msg in messages:
                try:
                    self.tg.send(msg[:4000])
                except Exception:
                    pass
        for msg in messages:
            print(msg)

    # ── 트리거 4: 포지션 청산 ──

    def on_position_close(self, position, close_reason: str, pnl_pct: float):
        """포지션 청산 시 — Shadow Logger에 기록"""
        sym = getattr(position, "symbol", "")
        ts = self._position_analysis_ts.pop(sym, "")

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

    # ── 유틸리티 ──

    def get_accuracy_report(self) -> dict:
        return self._shadow.generate_accuracy_report()

    def _send_result(self, consensus: MarketConsensus, trigger: str = ""):
        """정기/진입 분석 결과 전송"""
        prefix = f"[🤖 Agent {trigger}] "
        if self.tg:
            messages = self._formatter.format_consensus(consensus)
            for msg in messages:
                try:
                    self.tg.send(prefix + msg[:3900])
                except Exception:
                    pass

        if consensus.overall_risk == RiskLevel.DANGER:
            danger_msg = (f"🔴 DANGER — 에이전트 종합 판단: 위험\n"
                          f"경고: {', '.join(consensus.warnings[:3])}")
            if self.tg:
                try:
                    self.tg.send(danger_msg)
                except Exception:
                    pass

        if self.shadow_mode:
            trend = self._memory.get_risk_trend()
            print(f"  [Shadow] 리스크: {consensus.overall_risk.value} | "
                  f"추세: {trend} | 포지션: {len(consensus.position_verdicts)}개")

    def get_action_for_position(self, symbol: str) -> dict | None:
        """특정 포지션에 대한 최신 권고 조회."""
        if not self._orch or not self._orch._last_consensus:
            return None
        for v in self._orch._last_consensus.position_verdicts:
            if isinstance(v, dict) and v.get("symbol", "").replace("/", "") in symbol.replace("/", ""):
                return v
        return None
