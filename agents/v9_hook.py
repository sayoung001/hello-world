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
from agents.enriched_state import EnrichedStateBuilder

# Phase B: 리스크 관리 파이프라인
try:
    from risk.pipeline import RiskPipeline
    _HAS_RISK_PIPELINE = True
except ImportError:
    RiskPipeline = None
    _HAS_RISK_PIPELINE = False

# Phase C: 적응형 메모리 시스템
try:
    from agents.memory.rule_store import RuleStore
    from agents.memory.trading_brain import TradingBrain
    from agents.memory.reflection_engine import ReflectionEngine
    _HAS_MEMORY_SYSTEM = True
except ImportError:
    RuleStore = TradingBrain = ReflectionEngine = None
    _HAS_MEMORY_SYSTEM = False

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
                 analysis_interval_h: int = 4,
                 enable_risk_pipeline: bool = True,
                 enable_memory: bool = True):
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

        # [Phase D] 리스크 파이프라인 + 메모리 시스템
        self.enable_risk_pipeline = enable_risk_pipeline and _HAS_RISK_PIPELINE
        self.enable_memory = enable_memory and _HAS_MEMORY_SYSTEM

        self._risk_pipeline = RiskPipeline() if self.enable_risk_pipeline else None

        if self.enable_memory:
            self._rule_store = RuleStore()
            self._brain = TradingBrain(rule_store=self._rule_store)
            self._reflection = ReflectionEngine(rule_store=self._rule_store)
        else:
            self._rule_store = None
            self._brain = None
            self._reflection = None

        # [Phase D] 통합 상태 빌더
        self._state_builder = EnrichedStateBuilder(brain=self._brain)
        # 포지션별 진입 시 스냅샷 저장 (청산 시 반성 자료)
        self._entry_snapshots: dict[str, dict] = {}
        self._snapshot_lock = threading.Lock()

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

    def save_entry_snapshot(self, new_position):
        """포지션 오픈 시 스냅샷만 저장 (알림 없음, 반성용 데이터 수집)"""
        sym = getattr(new_position, "symbol", "")
        if not sym:
            return
        with self._snapshot_lock:
            self._entry_snapshots[sym] = {
                "entry_time": time.time(),
                "direction": getattr(new_position, "direction", ""),
                "entry_price": getattr(new_position, "entry_price", 0),
                "confidence": getattr(new_position, "confidence", 0),
            }

    def on_position_open(self, new_position, all_positions: list):
        """포지션 오픈 시 트리거 — 비동기 분석 + 텔레그램 전송"""
        self.save_entry_snapshot(new_position)
        pos_snapshot = list(all_positions)

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
        """포지션 청산 시 — Shadow Logger 기록 + 메모리 반성 자동화"""
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

        # [Phase D] 메모리 반성 자동화
        if self.enable_memory:
            self._reflect_on_close(position, outcome, pnl_pct)

    # ── [Phase D] 트리거 5: 진입 전 리스크 평가 ──

    def risk_check(self, signal: dict, positions: list | None = None,
                   market_data: dict | None = None,
                   vision_analysis: dict | None = None,
                   account_balance: float = 0.0,
                   recent_losses: int = 0) -> dict | None:
        """
        진입 전 리스크 평가 — Phase B 파이프라인 + Phase C 메모리 자문.

        Args:
            signal: 시그널 dict (symbol, direction, gap, confidence, adx, ...)
            positions: 현재 열린 포지션 리스트
            market_data: 시장 상태 스냅샷
            vision_analysis: Vision AI 분석 결과 (선택)
            account_balance: 계정 잔고
            recent_losses: 연속 손실 횟수

        Returns:
            {
                "risk_level": "low/medium/high/critical",
                "urgency": "low/medium/high",
                "recommended_leverage": int,
                "hedge_action": str,
                "brain_confidence": float,
                "brain_advice": str,
                "decision": RiskDecision,
                "enriched": dict,
                "approved": bool (medium 이하면 True),
            }
            또는 None (리스크 파이프라인 비활성화 시)
        """
        if not self.enable_risk_pipeline:
            return None

        try:
            # 1. 통합 상태 빌드 (Vision + RAG + Memory 결합)
            enriched = self._state_builder.build(
                signal=signal,
                positions=positions or [],
                market_data=market_data or {},
                vision_analysis=vision_analysis,
                account_balance=account_balance,
                recent_losses=recent_losses,
            )

            # 2. 리스크 파이프라인 실행
            ctx = enriched["pipeline_context"]
            decision = self._risk_pipeline.evaluate(ctx)

            # 3. 진입 스냅샷 저장 (청산 시 반성 자료)
            sym = signal.get("symbol", "")
            if sym:
                with self._snapshot_lock:
                    self._entry_snapshots[sym] = {
                        "signal": dict(signal),
                        "market_state": dict(market_data or {}),
                        "decision_risk": decision.risk_level.value,
                        "entry_time": time.time(),
                    }

            # 4. 응답 구성
            brain = enriched.get("brain_advice", {})
            approved = decision.risk_level.value in ("low", "medium")

            return {
                "risk_level": decision.risk_level.value,
                "urgency": decision.urgency.value,
                "recommended_leverage": decision.leverage_recommendation.recommended_leverage,
                "hedge_action": decision.hedge_recommendation.action.value,
                "brain_confidence": brain.get("brain_confidence", 0.5),
                "brain_advice": brain.get("advice", ""),
                "decision": decision,
                "enriched": enriched,
                "approved": approved,
            }
        except Exception as e:
            print(f"  [v9_hook] risk_check 실패: {e}")
            self._send_error("리스크 체크", e)
            return None

    # ── [Phase D] 반성 자동화 (내부) ──

    def _reflect_on_close(self, position, outcome: str, pnl_pct: float):
        """
        포지션 청산 시 자동 반성 → 메모리에 규칙 생성 + 승률 업데이트.
        별도 스레드에서 실행 (메인 루프 차단 방지).
        """
        def _run():
            try:
                sym = getattr(position, "symbol", "")
                direction = getattr(position, "direction", "")
                entry = getattr(position, "entry_price", 0)
                exit_price = getattr(position, "current_price", 0) or \
                             getattr(position, "last_price", entry)
                leverage = getattr(position, "leverage", 20)
                roe = pnl_pct * leverage

                hold = 0
                if hasattr(position, "hold_candles_15m"):
                    try:
                        hold = position.hold_candles_15m()
                    except Exception:
                        pass

                # 진입 스냅샷 복구
                with self._snapshot_lock:
                    snapshot = self._entry_snapshots.pop(sym, {})
                signal = snapshot.get("signal", {})
                market = snapshot.get("market_state", {})

                trade_record = {
                    "symbol": sym,
                    "direction": direction,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "exit_type": outcome,
                    "roe": roe,
                    "gap": float(signal.get("gap", 0)),
                    "confidence": signal.get("confidence", 0),
                    "adx": float(signal.get("adx", 0)),
                    "hold_candles": hold,
                    "direction_correct": (outcome == "TP") or (pnl_pct > 0),
                    "market_state": market,
                    "timestamp": time.time(),
                }

                # 1. 반성 → 규칙 생성
                reflection_result = self._reflection.reflect(trade_record)

                # 2. 브레인 통계 업데이트
                self._brain.record_trade_result(
                    sym, direction,
                    float(signal.get("gap", 0)),
                    outcome, roe,
                )

                # 3. 텔레그램 보고 (선택)
                if self.tg and reflection_result.get("lesson_learned"):
                    lesson = reflection_result["lesson_learned"]
                    new_rules = len(reflection_result.get("new_rules", []))
                    msg = (
                        f"🧠 [Reflection] {sym} {direction} {outcome}\n"
                        f"교훈: {lesson}\n"
                        f"생성 규칙: {new_rules}개"
                    )
                    try:
                        self.tg.send(msg)
                    except Exception:
                        pass

                print(f"  [Reflection] {sym}: {reflection_result.get('key_factor', '')} "
                      f"(method={reflection_result.get('method', '?')}, "
                      f"rules={len(reflection_result.get('new_rules', []))})")

                # 4. 유지보수 (24h 주기 감쇠)
                self._brain.run_maintenance()

            except Exception as e:
                print(f"  [v9_hook] 반성 실패: {e}")

        threading.Thread(target=_run, daemon=True).start()

    # ── [Phase D] 외부 조회 API ──

    def get_brain_advice(self, signal: dict,
                          market_state: dict | None = None) -> dict | None:
        """특정 시그널에 대한 Brain 자문만 조회 (리스크 파이프라인 없이)"""
        if not self.enable_memory:
            return None
        try:
            return self._brain.consult(signal, market_state=market_state)
        except Exception as e:
            print(f"  [v9_hook] brain 자문 실패: {e}")
            return None

    def get_memory_stats(self) -> dict | None:
        """메모리 시스템 통계"""
        if not self.enable_memory:
            return None
        try:
            brain_stats = self._brain.get_stats()
            reflection_stats = self._reflection.stats
            return {
                "brain": brain_stats,
                "reflection": reflection_stats,
            }
        except Exception as e:
            print(f"  [v9_hook] 메모리 통계 실패: {e}")
            return None

    # ── [Phase F] 온디맨드 분석 (텔레그램 명령) ──

    def analyze_position(self, symbol: str, positions: list) -> str:
        """특정 코인 포지션 분석 → 텔레그램 메시지 반환"""
        matching = [p for p in positions if getattr(p, "symbol", "") == symbol]
        if not matching:
            return f"❌ {symbol} 포지션 없음"

        try:
            pos_infos = self._convert_positions(matching)
            orch = self._get_orchestrator()
            consensus = orch.run(positions=pos_infos)
            self._memory.store(consensus)
            self._shadow.log_analysis(consensus, trigger="수동요청")

            messages = self._formatter.format_consensus(consensus)
            return f"[🔎 포지션 판결 — {symbol}]\n" + "\n".join(messages)
        except Exception as e:
            return f"❌ {symbol} 분석 실패: {e}"

    def analyze_entry(self, symbol: str, exchange=None) -> str:
        """특정 코인 진입 판단 → 텔레그램 메시지 반환"""
        try:
            if not exchange:
                return "❌ exchange 미연결"

            ohlcv = exchange.fetch_ohlcv(symbol, '15m', limit=201)
            import pandas as pd
            df = pd.DataFrame(ohlcv, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df['Date'] = pd.to_datetime(df['Date'], unit='ms')
            df.set_index('Date', inplace=True)
            if len(df) > 0:
                now_ts = pd.Timestamp.now(tz='UTC')
                last_start = df.index[-1]
                if hasattr(last_start, 'tz') and last_start.tz is None:
                    last_start = last_start.tz_localize('UTC')
                if now_ts < last_start + pd.Timedelta(minutes=15):
                    df = df.iloc[:-1]
            if len(df) < 100:
                return f"❌ {symbol} 데이터 부족 ({len(df)}봉)"

            from convergence_strategy import ConvergenceBreakout
            strategy = ConvergenceBreakout()
            result = strategy.detect(df)

            short_sym = symbol.replace('/USDT', '')
            close = df['Close'].astype(float)
            ema12 = close.ewm(span=12).mean().iloc[-1]
            ema26 = close.ewm(span=26).mean().iloc[-1]
            gap = abs(ema12 - ema26) / close.iloc[-1] * 100
            adx_val = result.get('details', {}).get('adx', 0) if result else 0
            rsi_val = result.get('details', {}).get('rsi', 50) if result else 50

            lines = [f"🔎 진입 판단 — {short_sym}"]
            lines.append(f"{'━' * 25}")
            lines.append(f"EMA GAP: {gap:.3f}%")
            lines.append(f"ADX: {adx_val:.1f} | RSI: {rsi_val:.1f}")
            lines.append(f"현재가: {close.iloc[-1]:,.2f}")

            if not result:
                lines.append(f"\n📋 시그널: 없음 (수렴 미감지)")
                lines.append(f"{'━' * 25}")
                return "\n".join(lines)

            direction = result.get('direction', '?')
            conf = result.get('confidence', 0)
            lines.append(f"\n📋 시그널: {direction.upper()}")
            lines.append(f"신뢰도: {conf} | 방향: {direction}")

            signal = {
                "symbol": symbol, "direction": direction,
                "confidence": conf, "gap": gap, "adx": adx_val,
                "adx_value": adx_val, "rsi": rsi_val,
            }

            if self.enable_risk_pipeline:
                risk_result = self.risk_check(signal=signal)
                if risk_result:
                    risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}
                    rl = risk_result.get("risk_level", "?")
                    lines.append(f"\n리스크: {risk_emoji.get(rl, '⚪')} {rl.upper()}")
                    lines.append(f"레버리지 권고: {risk_result.get('recommended_leverage', 20)}x")
                    approved = risk_result.get("approved", True)
                    lines.append(f"진입 승인: {'✅ 가능' if approved else '🛑 거부'}")

            if self.enable_memory:
                brain = self.get_brain_advice(signal)
                if brain:
                    lines.append(f"\n🧠 Brain 신뢰도: {brain.get('brain_confidence', 0):.2f}")
                    advice = brain.get("advice", "")
                    if advice:
                        lines.append(f"자문: {advice[:120]}")

            lines.append(f"{'━' * 25}")
            return "\n".join(lines)

        except Exception as e:
            return f"❌ {symbol} 진입 분석 실패: {e}"

    def analyze_market_now(self, positions: list) -> str:
        """즉시 시장 분석 → 텔레그램 메시지 반환"""
        try:
            pos_infos = self._convert_positions(list(positions)) if positions else []
            orch = self._get_orchestrator()
            consensus = orch.run(positions=pos_infos)
            self._memory.store(consensus)
            self._shadow.log_analysis(consensus, trigger="수동요청")
            self._last_analysis_time = time.time()

            messages = self._formatter.format_consensus(consensus)
            return f"[🔎 시장 분석 (수동)]\n" + "\n".join(messages)
        except Exception as e:
            return f"❌ 시장 분석 실패: {e}"

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
