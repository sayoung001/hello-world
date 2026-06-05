"""
auto_trader_v9_bot2.py — BTC 돌파 후 알트 연쇄 돌파 추격 봇
═══════════════════════════════════════════════════════════════
Bot 1: BTC 조용할 때 알트 독자 돌파 (기존 v9)
Bot 2: BTC 돌파 직후 알트 연쇄 돌파 캐치 (이 파일)

★ Bot1과 별도 바이낸스 계정 사용
  - 포지션 파일 분리 (positions_v9_bot2.json)
  - Bot2 전용 API 키 (BINANCE_API_KEY_BOT2)
  - 독립 마진 관리 (Bot1 마진 불참조)

사용법:
  python auto_trader_v9_bot2.py --mode E              # 페이퍼
  python auto_trader_v9_bot2.py --mode E --live        # 실전
  python auto_trader_v9_bot2.py --mode E --live --hunt-hours 3
"""

import ccxt, pandas as pd, numpy as np, time, json, os, sys, requests, traceback, math
import threading
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional, Dict, List

from convergence_strategy import ConvergenceBreakout, CONVERGENCE_CONFIG
from dotenv import load_dotenv
load_dotenv()

# Bot1 클래스 재활용
from auto_trader_v9 import (
    Telegram, Executor, BTCTrend7Level, OnchainFetcher,
    Position, KST, BINANCE_FAPI, MODE_CONFIGS, PROFILES
)

# Phase A~F 에이전트 시스템
try:
    from agents.v9_hook import AgentHook
except ImportError:
    AgentHook = None

# ═══════════════════════════════════════════════════════════
#  Bot2 전용 설정
# ═══════════════════════════════════════════════════════════

# Bot1 워치리스트 (참고용 — 별도 계정이므로 제외하지 않음)
BOT1_WATCHLIST = {
    "SOL/USDT", "XRP/USDT", "NEAR/USDT", "UNI/USDT", "COMP/USDT",
    "SAND/USDT", "MANA/USDT", "QNT/USDT", "ANKR/USDT", "SUI/USDT",
    "HBAR/USDT", "KAS/USDT", "NTRN/USDT", "WAXP/USDT", "TRX/USDT",
    "ENJ/USDT", "WLD/USDT", "TRUMP/USDT", "1000PEPE/USDT",
    "MORPHO/USDT", "SEI/USDT", "GALA/USDT",
}

CONFIG_BOT2 = {
    "PAPER_TRADE": True,

    # API Keys (Bot2 전용 바이낸스 계정, Bot1과 별도)
    "BINANCE_API_KEY":  os.environ.get("BINANCE_API_KEY_BOT2",
                        os.environ.get("BINANCE_API_KEY", "")),
    "BINANCE_SECRET":   os.environ.get("BINANCE_SECRET_BOT2",
                        os.environ.get("BINANCE_SECRET", "")),
    "COINGLASS_API_KEY": os.environ.get("COINGLASS_API_KEY", ""),
    "TELEGRAM_TOKEN":   os.environ.get("TELEGRAM_TOKEN_TRADER",
                        os.environ.get("TELEGRAM_TOKEN_MONITOR", "")),
    "TELEGRAM_CHAT_ID": os.environ.get("TELEGRAM_CHAT_ID", ""),

    # 워치리스트: 바이낸스에서 자동 수집 (Bot1 제외)
    "WATCHLIST": [],   # 런타임에 채워짐
    "AUTO_WATCHLIST": True,
    "MIN_DAILY_VOLUME_USDT": 10_000_000,  # 일거래량 1000만$ 이상만

    # 레버리지 + 수수료
    "LEVERAGE": 20,
    "FEE_RATE": 0.0004,

    # 시그널 필터 (Bot1보다 느슨 — hunt mode 특성)
    "EMA_GAP_THRESHOLD": 0.7,
    "MIN_SQUEEZE_CANDLES": 6,     # Bot1: 15 → Bot2: 6 (더 짧은 스퀴즈 허용)
    "MIN_CONFIDENCE": 50,          # Bot1: 70 → Bot2: 50 (낮은 확신도 허용)
    "MIN_ADX": 18,                 # Bot1: 25 → Bot2: 18
    "MIN_VOLUME_RATIO": 1.3,       # Bot1: 1.5 → Bot2: 1.3
    "MIN_VOL_SURGE": 1.5,         # Bot2 전용: vol surge 필터 (2.0→1.5 완화)

    # 모멘텀 시그널 (스퀴즈 없이도 BTC 방향 모멘텀으로 진입)
    "MOMENTUM_ENABLED": True,      # 스퀴즈 미충족 시 모멘텀 시그널 사용
    "MOM_ATR_MULT": 2.0,           # ATR 대비 N배 이상 움직임
    "MOM_VOL_SURGE": 2.5,          # 모멘텀: 거래량 2.5배 이상 (스퀴즈 없으므로 더 엄격)
    "MOM_RSI_LONG": 55,            # 모멘텀 롱: RSI > 55
    "MOM_RSI_SHORT": 45,           # 모멘텀 숏: RSI < 45
    "FIBO_LEVEL": 2.618,
    "SL_RATIO": 1.5,

    # 전략 (MODE_CONFIGS에서 오버라이드)
    "STRATEGY": "Cascade",
    "LOCK_FIBO": 2.0,
    "TRAIL_PCT": 0,
    "TP_FIBO": 8.0,
    "ENABLE_EARLY_EXIT": True,
    "EE_START_CANDLE": 16,
    "EE_END_CANDLE": 24,
    "EE_THRESHOLD_ROE": -20,
    "TIMEOUT_H": 192,

    # BTC 필터 — Bot2는 squeeze 사용 안 함
    "BTC_FILTER_ENABLED": True,
    "BTC_SQUEEZE_ENABLED": False,  # ★ Bot2 핵심: squeeze OFF

    # ★ Bot2 전용: Hunt 설정
    "HUNT_WINDOW_HOURS": 4,        # trigger 후 스캔 지속 시간 (3→4h)
    "HUNT_COOLDOWN_HOURS": 1,      # hunt 종료 후 대기
    "BTC_TRIGGER_GAP": 0.5,        # BTC gap이 이 값 넘으면 trigger
    "BTC_GAP_ACCEL": 0.15,         # gap 가속도 (이전 대비 +0.15% 이상이면 trigger)

    # 포지션 — Bot1과 분리
    "MAX_POSITIONS": 5,
    "RISK_PCT": 8.0,               # Bot1(10%)보다 보수적
    "SIZING": "compound",
    "MARGIN_EXPOSURE_LIMIT": 0.50,  # Bot2 독립 계정 — 50% 사용
    "MAX_DAILY_TRADES": 8,

    # 주기
    "SCAN_SEC": 60,
    "MONITOR_SEC": 15,
    "STATUS_SEC": 7200,
    "CG_CACHE_SEC": 120,

    # 경로 — Bot1과 분리
    "LOG_DIR": "./trade_logs",
    "POSITIONS_FILE": "./trade_logs/positions_v9_bot2.json",
}


# ═══════════════════════════════════════════════════════════
#  BTC 돌파 상태 머신
# ═══════════════════════════════════════════════════════════

class BTCBreakoutTracker:
    """
    BTC squeeze → breakout 전환을 감지하는 상태 머신.

    상태:
      STANDBY  — BTC squeeze 중, Bot2 대기
      TRIGGER  — BTC squeeze 해제 감지! 방향 기록
      HUNT     — 알트 스캔 활성 (N시간 제한)
      COOLDOWN — hunt 종료, 다음 squeeze까지 대기

    트리거 조건 (OR):
      1. squeeze → non-squeeze 전환 (EMA gap 0.5% 이하 → 초과)
      2. BTC gap이 BTC_TRIGGER_GAP 이상으로 급변 (gap 기반 돌파)
    """
    STANDBY = "STANDBY"
    TRIGGER = "TRIGGER"
    HUNT = "HUNT"
    COOLDOWN = "COOLDOWN"

    def __init__(self, btc_trend: BTCTrend7Level, cfg: dict):
        self.btc = btc_trend
        self.cfg = cfg
        self.state = self.STANDBY
        self.hunt_direction = None       # "long" or "short"
        self.hunt_start_time = None
        self.last_squeeze_state = None   # None = 초기 상태 (첫 루프에서 설정)
        self._cooldown_until = 0
        self._last_gap = 0.0            # 이전 gap 기록 (급변 감지용)

    def update(self) -> str:
        """매 루프마다 호출 — 상태 전환 로직

        트리거 조건 (OR, 어느 하나라도 충족 시 HUNT 진입):
          1. squeeze → non-squeeze 전환
          2. gap이 BTC_TRIGGER_GAP 이상으로 돌파
          3. gap 가속 — 이전 대비 BTC_GAP_ACCEL(0.15%) 이상 확대
        """
        self.btc.update()
        now = time.time()
        is_squeeze = self.btc.is_squeeze
        gap = self.btc.gap_pct
        trigger_gap = self.cfg.get("BTC_TRIGGER_GAP", 0.5)
        gap_accel = self.cfg.get("BTC_GAP_ACCEL", 0.15)

        if self.state == self.STANDBY:
            if self.last_squeeze_state is None:
                self.last_squeeze_state = is_squeeze
                self._last_gap = gap
                return self.state

            triggered = False
            trigger_reason = ""

            # 조건 1: squeeze → non-squeeze 전환
            if self.last_squeeze_state and not is_squeeze:
                triggered = True
                trigger_reason = "squeeze_breakout"

            # 조건 2: gap이 trigger_gap 이상으로 돌파
            if not triggered and self._last_gap < trigger_gap and gap >= trigger_gap:
                triggered = True
                trigger_reason = "gap_threshold"

            # 조건 3: gap 가속 (이미 높더라도 더 확대되면 모멘텀 추격)
            if not triggered and gap >= trigger_gap:
                gap_delta = gap - self._last_gap
                if gap_delta >= gap_accel:
                    triggered = True
                    trigger_reason = f"gap_accel(+{gap_delta:.3f}%)"

            if triggered:
                self.state = self.HUNT
                if self.btc._ema_s > self.btc._ema_l:
                    self.hunt_direction = "long"
                else:
                    self.hunt_direction = "short"
                self.hunt_start_time = now
                self._trigger_reason = trigger_reason
                print(f"  [BTC] HUNT 트리거: {trigger_reason} | gap:{gap:.3f}% "
                      f"prev:{self._last_gap:.3f}% dir:{self.hunt_direction}")

            self.last_squeeze_state = is_squeeze
            self._last_gap = gap

        elif self.state == self.HUNT:
            elapsed_h = (now - self.hunt_start_time) / 3600
            hunt_hours = self.cfg.get("HUNT_WINDOW_HOURS", 4)

            # HUNT 중에도 gap 방향 전환 시 방향 업데이트
            if self.btc._ema_s > self.btc._ema_l:
                self.hunt_direction = "long"
            else:
                self.hunt_direction = "short"

            if elapsed_h >= hunt_hours:
                self.state = self.COOLDOWN
                cooldown_h = self.cfg.get("HUNT_COOLDOWN_HOURS", 1)
                self._cooldown_until = now + cooldown_h * 3600
                self.hunt_direction = None
            self._last_gap = gap

        elif self.state == self.COOLDOWN:
            if now >= self._cooldown_until:
                self.state = self.STANDBY
                self.last_squeeze_state = is_squeeze
                self._last_gap = gap

        elif self.state == self.TRIGGER:
            self.state = self.HUNT

        return self.state

    @property
    def is_hunting(self):
        return self.state == self.HUNT

    @property
    def hunt_elapsed_min(self):
        if self.hunt_start_time:
            return (time.time() - self.hunt_start_time) / 60
        return 0

    def status_str(self):
        btc_s = self.btc.status_str()
        gap = self.btc.gap_pct
        tg = self.cfg.get("BTC_TRIGGER_GAP", 0.5)
        hunt_s = ""
        if self.state == self.HUNT:
            d = self.hunt_direction or "?"
            elapsed = self.hunt_elapsed_min
            window = self.cfg.get("HUNT_WINDOW_HOURS", 3) * 60
            hunt_s = f" | HUNT {d} ({elapsed:.0f}/{window:.0f}min)"
        elif self.state == self.COOLDOWN:
            remain = max(0, self._cooldown_until - time.time()) / 60
            hunt_s = f" | COOLDOWN ({remain:.0f}min)"
        elif self.state == self.STANDBY:
            sq = "SQZ" if self.btc.is_squeeze else "NO_SQZ"
            hunt_s = f" | gap:{gap:.3f}% trig:{tg}% {sq}"
        return f"[{self.state}]{hunt_s} | {btc_s}"


# ═══════════════════════════════════════════════════════════
#  Bot2 메인 트레이더
# ═══════════════════════════════════════════════════════════

class HuntTrader:
    def __init__(self, cfg=None):
        self.cfg = cfg or CONFIG_BOT2
        self.bot_tag = "🎯Bot2"
        self.mode = self.cfg.get("_mode", "E")
        self.mode_cfg = MODE_CONFIGS[self.mode]

        # 모드 오버라이드
        for k in ['STRATEGY','LOCK_FIBO','TRAIL_PCT','TP_FIBO',
                   'EE_START_CANDLE','EE_END_CANDLE','EE_THRESHOLD_ROE']:
            self.cfg[k] = self.mode_cfg[k]

        # 프로필
        self.profile_name = self.cfg.get("_profile", "standard")
        self.profile = PROFILES[self.profile_name]

        os.makedirs(self.cfg["LOG_DIR"], exist_ok=True)

        # 거래소
        self.exchange = ccxt.binance({
            'apiKey': self.cfg["BINANCE_API_KEY"],
            'secret': self.cfg["BINANCE_SECRET"],
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        self.tg = Telegram(self.cfg["TELEGRAM_TOKEN"], self.cfg["TELEGRAM_CHAT_ID"])
        self.executor = Executor(self.exchange, self.cfg, self.tg)
        self.executor.setup_hedge_mode()

        # 전략
        self.strategy = ConvergenceBreakout({**CONVERGENCE_CONFIG,
            'EMA_GAP_THRESHOLD': self.cfg["EMA_GAP_THRESHOLD"],
            'MIN_SQUEEZE_CANDLES': self.cfg["MIN_SQUEEZE_CANDLES"],
            'MIN_CONFIDENCE': self.cfg["MIN_CONFIDENCE"],
            'MIN_ADX': self.cfg["MIN_ADX"],
            'TP_FIBO_LEVEL': self.cfg["FIBO_LEVEL"]})

        # BTC 트렌드 + 돌파 트래커
        self.btc_trend = BTCTrend7Level(self.exchange, cache_sec=30)  # 30초 (빠른 감지)
        self.btc_trend.update()
        self.hunt_tracker = BTCBreakoutTracker(self.btc_trend, self.cfg)

        # 워치리스트 자동 구성
        if self.cfg.get("AUTO_WATCHLIST", True):
            self._build_watchlist()

        # 포지션
        self.positions: List[Position] = []
        self._load_positions()
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self._daily_reset_done = False
        self._alerted_orders = set()
        self._scan_fails = {}
        self._init_balance = self.executor.total_balance()

        # [Phase A~F] 에이전트 시스템 (Shadow Mode)
        self.agent_hook = None
        if AgentHook is not None:
            try:
                self.agent_hook = AgentHook(
                    cg_client=None,
                    btc_filter=self.btc_trend,
                    tg=self.tg,
                    exchange=self.exchange,
                    shadow_mode=True,
                    analysis_interval_h=4,
                )
                print(f"  {self.bot_tag} ✅ 에이전트 시스템 초기화 (Shadow Mode)")
            except Exception as e:
                print(f"  {self.bot_tag} ⚠️ 에이전트 초기화 실패 (봇 정상 운영): {e}")
                self.agent_hook = None

        # 시작 알람
        self._send_startup_msg()

    def _build_watchlist(self):
        """바이낸스 선물 전체 USDT 코인 수집 (별도 계정 — Bot1 제외 없음)"""
        try:
            markets = self.exchange.load_markets()
            watchlist = []
            for sym, info in markets.items():
                if not info.get('active'): continue
                if info.get('type') != 'swap': continue
                clean_sym = sym.split(':')[0] if ':' in sym else sym
                if not clean_sym.endswith('/USDT'): continue
                if clean_sym in ('BTC/USDT',): continue
                if clean_sym not in watchlist:
                    watchlist.append(clean_sym)

            self.cfg["WATCHLIST"] = sorted(watchlist)
            print(f"  {self.bot_tag} 워치리스트: {len(watchlist)}개")
        except Exception as e:
            print(f"  워치리스트 자동 구성 실패: {e}")
            self.cfg["WATCHLIST"] = [
                "AAVE/USDT", "ALGO/USDT", "ATOM/USDT", "AVAX/USDT",
                "AXS/USDT", "BNB/USDT", "CFX/USDT", "DOGE/USDT",
                "DOT/USDT", "ETC/USDT", "FIL/USDT", "INJ/USDT",
                "LDO/USDT", "LINK/USDT", "MATIC/USDT", "OP/USDT",
                "RENDER/USDT", "SHIB/USDT", "TON/USDT", "XLM/USDT",
            ]

    def _send_startup_msg(self):
        bal = self._init_balance
        trade_mode = "페이퍼" if self.cfg["PAPER_TRADE"] else "실전"
        n_coins = len(self.cfg["WATCHLIST"])
        hunt_h = self.cfg.get("HUNT_WINDOW_HOURS", 3)

        mom_s = "ON" if self.cfg.get("MOMENTUM_ENABLED") else "OFF"
        msg = (f"{self.bot_tag} 시작 [모드{self.mode} {self.mode_cfg['desc']}]\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"전략: BTC 돌파 → 알트 연쇄 추격\n"
               f"매매: {trade_mode} | 잔고: ${bal:,.0f}\n"
               f"코인: {n_coins}개 | Hunt: {hunt_h}h\n"
               f"R:{self.cfg['RISK_PCT']}% P:{self.cfg['MAX_POSITIONS']}\n"
               f"수렴: conf≥{self.cfg['MIN_CONFIDENCE']} ADX≥{self.cfg['MIN_ADX']} "
               f"vol≥{self.cfg['MIN_VOL_SURGE']}\n"
               f"모멘텀: {mom_s} ATR×{self.cfg.get('MOM_ATR_MULT',2.0)} "
               f"vol×{self.cfg.get('MOM_VOL_SURGE',2.5)}\n"
               f"BTC: {self.hunt_tracker.status_str()}")
        self.tg.send(msg)
        print(msg)

    # ── 모멘텀 시그널 (스퀴즈 없이 BTC 방향 추종) ──

    def _detect_momentum(self, df, sym) -> dict | None:
        """BTC 돌파 방향으로 강한 모멘텀을 보이는 알트 감지.
        스퀴즈 조건 없이 가격+거래량 급등만으로 시그널 생성."""
        if len(df) < 60:
            return None

        df = self.strategy._ensure_indicators(df)
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        atr = curr.get('atr', 0)
        if atr <= 0:
            return None

        price = curr['Close']
        move = abs(curr['Close'] - prev['Close'])
        breakout_atr = move / atr

        if breakout_atr < self.cfg.get("MOM_ATR_MULT", 2.0):
            return None

        vol_surge = self._calc_vol_surge(df)
        if vol_surge < self.cfg.get("MOM_VOL_SURGE", 2.5):
            return None

        adx = curr.get('adx', 0)
        if adx < self.cfg["MIN_ADX"]:
            return None

        rsi_col = 'rsi_14' if 'rsi_14' in curr.index else 'rsi'
        rsi = curr.get(rsi_col, 50)

        is_bullish = curr['Close'] > curr['Open']
        is_bearish = curr['Close'] < curr['Open']

        direction = None
        if is_bullish and rsi > self.cfg.get("MOM_RSI_LONG", 55):
            direction = "long"
        elif is_bearish and rsi < self.cfg.get("MOM_RSI_SHORT", 45):
            direction = "short"

        if not direction:
            return None

        # SL/TP 계산 (ATR 기반)
        atr_val = float(atr)
        sl_dist = atr_val * 1.5
        tp_dist = atr_val * 3.0
        if direction == "long":
            sl_price = price - sl_dist
            tp_price = price + tp_dist
        else:
            sl_price = price + sl_dist
            tp_price = price - tp_dist

        rvol = curr.get('rvol', vol_surge)
        conf = min(90, int(40 + breakout_atr * 10 + vol_surge * 5))

        return {
            'symbol': sym,
            'direction': direction,
            'entry_price': price,
            'stop_loss': sl_price,
            'take_profit': tp_price,
            'confidence': conf,
            'atr': atr_val,
            'reasons': [f'momentum_atr×{breakout_atr:.1f}', f'vol_surge×{vol_surge:.1f}'],
            'squeeze_candles': 0,
            'vol_surge': vol_surge,
            'adx_value': adx, 'adx': adx,
            'gap': 0, 'volume_ratio': rvol, 'rsi': rsi,
            'details': {'adx': adx, 'gap_pct': 0, 'rsi': rsi,
                        'breakout': {'volume_ratio': rvol}},
            '_signal_type': 'momentum',
        }

    # ── 스캔 (hunt 모드에서만 활성) ──

    @staticmethod
    def _calc_vol_surge(df, lookback=20):
        if len(df) < lookback + 1: return 0.0
        cur = df['Volume'].iloc[-1]
        avg = df['Volume'].iloc[-(lookback+1):-1].mean()
        return round(cur / avg, 2) if avg > 0 else 0.0

    def scan(self):
        """hunt 모드에서만 알트 스캔 — 수렴 돌파 + 모멘텀 듀얼 시그널"""
        signals = []

        for sym in self.cfg["WATCHLIST"]:
            try:
                ohlcv = self.exchange.fetch_ohlcv(sym, '15m', limit=201)
                df = pd.DataFrame(ohlcv, columns=['Date','Open','High','Low','Close','Volume'])
                df['Date'] = pd.to_datetime(df['Date'], unit='ms')
                df.set_index('Date', inplace=True)

                # 미완성 봉 제거
                now_ts = pd.Timestamp.now(tz='UTC')
                if len(df) > 0:
                    last_start = df.index[-1]
                    if hasattr(last_start, 'tz') and last_start.tz is None:
                        last_start = last_start.tz_localize('UTC')
                    if now_ts < last_start + pd.Timedelta(minutes=15):
                        df = df.iloc[:-1]
                if len(df) < 100: continue

                # 시도 1: 수렴 돌파 시그널 (기존 전략)
                result = self.strategy.detect(df)
                if result:
                    conf = result.get('confidence', 0)
                    if conf >= self.cfg["MIN_CONFIDENCE"]:
                        result['symbol'] = sym
                        adx = result.get('details',{}).get('adx',0)
                        vr = result.get('details',{}).get('breakout',{}).get('volume_ratio',0)
                        rsi = result.get('details',{}).get('rsi',50)

                        if adx >= self.cfg["MIN_ADX"] and vr >= self.cfg["MIN_VOLUME_RATIO"]:
                            vol_surge = self._calc_vol_surge(df)
                            if vol_surge >= self.cfg.get("MIN_VOL_SURGE", 1.5):
                                result['vol_surge'] = vol_surge
                                gap_pct_val = result.get('details', {}).get('gap_pct', 0)
                                result.update({'adx_value': adx, 'adx': adx,
                                               'gap': gap_pct_val, 'volume_ratio': vr, 'rsi': rsi})
                                result['_signal_type'] = 'convergence'
                                signals.append(result)
                                time.sleep(0.08)
                                self._scan_fails[sym] = 0
                                continue  # 수렴 시그널 발견 → 모멘텀 스킵

                # 시도 2: 모멘텀 시그널 (스퀴즈 없어도 강한 움직임)
                if self.cfg.get("MOMENTUM_ENABLED", True):
                    mom = self._detect_momentum(df, sym)
                    if mom:
                        signals.append(mom)

                time.sleep(0.08)
                self._scan_fails[sym] = 0

            except Exception as e:
                self._scan_fails[sym] = self._scan_fails.get(sym, 0) + 1
                if self._scan_fails[sym] % 10 == 5:
                    self.tg.send_error(f"{self.bot_tag} {sym} 스캔 {self._scan_fails[sym]}회 실패: {e}")
                continue

        return signals

    # ── 진입 (BTC 방향 필터) ──

    def try_enter(self, signal):
        sym = signal['symbol']
        d = signal['direction']

        # 포지션 제한
        if len(self.positions) >= self.cfg["MAX_POSITIONS"]: return
        if any(p.symbol == sym for p in self.positions): return
        if self.daily_trades >= self.cfg["MAX_DAILY_TRADES"]: return

        # ★ Bot2 핵심: BTC 돌파 방향과 같은 방향만 진입
        hunt_dir = self.hunt_tracker.hunt_direction
        if hunt_dir and d != hunt_dir:
            print(f"  {self.bot_tag} {sym} {d} → BTC방향({hunt_dir})과 다름 → SKIP")
            return

        # BTC 7Level 방향 필터 (CRASH 시 롱차단, STRONG_BULL 시 숏차단)
        if self.btc_trend:
            if d == 'long' and self.btc_trend.long_blocked:
                print(f"  {self.bot_tag} {sym} {d} → BTC CRASH 롱차단 → SKIP")
                return
            if d == 'short' and self.btc_trend.short_blocked:
                print(f"  {self.bot_tag} {sym} {d} → BTC STRONG_BULL 숏차단 → SKIP")
                return

        # [Phase F] 진입 전 리스크 평가
        if self.agent_hook is not None:
            try:
                risk_result = self.agent_hook.risk_check(
                    signal=signal,
                    positions=[p.__dict__ for p in self.positions] if self.positions else [],
                    account_balance=self.executor.total_balance(),
                )
                if risk_result and not risk_result.get("approved", True):
                    print(f"  {self.bot_tag} 🛑 리스크 차단: {sym} {d} — "
                          f"레벨: {risk_result.get('risk_level', '?')}")
                    return
            except Exception as e:
                print(f"  {self.bot_tag} [Phase F] risk_check 오류 (진입 계속): {e}")

        # 마진 체크 (Bot2 독립 계정)
        entry = signal['entry_price']
        if entry <= 0: return  # [V9.2] 비정상 시그널 방어
        sl_dist = abs(entry - signal['stop_loss']) * self.cfg["SL_RATIO"]
        if sl_dist <= 0: return
        sl = entry - sl_dist if d == 'long' else entry + sl_dist
        sl_dist_pct = sl_dist / entry * 100

        bal = self.executor.total_balance()
        if bal <= 0: return  # API 실패 시 진입 차단

        current_margin = sum(p.quantity * p.entry_price / self.cfg["LEVERAGE"]
                             for p in self.positions)
        remaining = bal * self.cfg["MARGIN_EXPOSURE_LIMIT"] - current_margin
        if remaining <= 0:
            print(f"  {self.bot_tag} 마진 부족: 잔고${bal:.0f} 사용${current_margin:.0f}")
            return

        sizing_base = max(bal, self._init_balance * 0.3)
        risk_pct = self.cfg["RISK_PCT"]
        pos_notional = sizing_base * (risk_pct / 100) / (sl_dist_pct / 100)
        max_notional = remaining * self.cfg["LEVERAGE"]
        pos_notional = min(pos_notional, max_notional)
        if pos_notional <= 0: return

        qty = pos_notional / entry
        margin = pos_notional / self.cfg["LEVERAGE"]
        if margin < 5: return

        # TP 계산 (E모드 Cascade 동일)
        fibo_base = self.cfg["FIBO_LEVEL"]
        lock_fibo = self.cfg["LOCK_FIBO"]
        tp_fibo = self.cfg["TP_FIBO"]
        tp_dist_orig = abs(signal['take_profit'] - entry)
        if tp_dist_orig <= 0: return  # [V9.2] TP 거리 0 방어
        lock_ratio = lock_fibo / fibo_base
        lock_dist = tp_dist_orig * lock_ratio
        lock_roe = lock_dist / entry * 100

        if tp_fibo > 0:
            tp_ratio = tp_fibo / fibo_base
            exchange_tp = entry + tp_dist_orig * tp_ratio if d == 'long' else entry - tp_dist_orig * tp_ratio
        else:
            safety_ratio = 8.0 / fibo_base
            exchange_tp = entry + tp_dist_orig * safety_ratio if d == 'long' else entry - tp_dist_orig * safety_ratio

        # 체결
        result = self.executor.open_position(sym, d, qty, entry, sl, exchange_tp)
        if not result: return

        ap = result['price']
        btc_state = self.btc_trend.trend_name if self.btc_trend else "N/A"
        vol_surge_v = signal.get('vol_surge', 0)

        pos = Position(
            symbol=sym, direction=d, entry_price=ap, quantity=result['qty'],
            stop_loss=sl, take_profit=exchange_tp,
            atr=signal.get('atr', ap*0.01),
            entry_time=datetime.now(KST).isoformat(),
            confidence=signal['confidence'],
            reasons=signal.get('reasons',[]),
            squeeze_candles=signal.get('squeeze_candles',0),
            original_sl=sl, lock_trigger_roe=lock_roe,
            sl_order_id=result.get('sl_order_id',''),
            tp_order_id=result.get('tp_order_id',''),
            adx_value=signal.get('adx_value',0),
            volume_ratio=signal.get('volume_ratio',0),
            rsi=signal.get('rsi',50),
            gap_pct=signal.get('details',{}).get('gap_pct',0),
            vol_surge=vol_surge_v,
            entry_btc_state=btc_state,
            profile=self.profile_name,
        )
        self.positions.append(pos)
        self._save_positions()
        self.daily_trades += 1

        # [Phase D] 에이전트 — 진입 스냅샷만 저장 (자동 알림 제거, /b2판결로 대체)
        if self.agent_hook:
            try:
                self.agent_hook.save_entry_snapshot(pos)
            except Exception:
                pass

        # 진입 알람
        kr_dir = "롱 📈" if d == 'long' else "숏 📉"
        gap_pct = signal.get('details',{}).get('gap_pct',0)
        sq_candles = signal.get('squeeze_candles',0)
        hunt_min = self.hunt_tracker.hunt_elapsed_min
        msg = (f"{self.bot_tag} 🟢 진입 | {kr_dir} {sym}\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"🎯 BTC {self.hunt_tracker.hunt_direction or '?'} 돌파 추격\n"
               f"  hunt {hunt_min:.0f}분째 | BTC gap:{self.btc_trend.gap_pct:.3f}%\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"📋 시그널:\n"
               f"  conf:{signal['confidence']} GAP:{gap_pct:.2f}% sqz:{sq_candles}\n"
               f"  ADX:{signal.get('adx_value',0):.0f} vol:×{signal.get('volume_ratio',0):.1f}"
               f" surge:×{vol_surge_v:.1f}\n"
               f"━━━━━━━━━━━━━━━━━━━━\n"
               f"💰 진입: {ap:,.6f}\n"
               f"  SL: {sl:,.6f} | TP: {exchange_tp:,.6f}\n"
               f"  마진: ${margin:.0f} | 잔고: ${bal:,.0f}")
        self.tg.send(msg)

    # ── 포지션 관리 (Bot1과 동일 로직) ──

    def manage_positions(self):
        """Bot1의 manage_positions와 동일 — Cascade/CasTrail"""
        closed = []
        for pos in self.positions:
            price = self.executor.price(pos.symbol)
            if price <= 0: continue
            pos.update_extremes(price)
            roe_spot = pos.current_roe(price)
            roe_lev = roe_spot * self.cfg["LEVERAGE"]
            hc = pos.hold_candles_15m()
            hh = pos.hold_h()

            # SL
            sl_hit = ((pos.direction == 'long' and price <= pos.stop_loss) or
                      (pos.direction == 'short' and price >= pos.stop_loss))
            if sl_hit:
                reason = 'SL_본전' if pos.sl_locked else 'SL'
                self._close(pos, price, reason, closed); continue

            # TP
            tp_hit = ((pos.direction == 'long' and price >= pos.take_profit) or
                      (pos.direction == 'short' and price <= pos.take_profit))
            if tp_hit:
                self._close(pos, price, f'TP_F{self.cfg["TP_FIBO"]}', closed); continue

            # Lock: F2.0 → SL 본전
            if not pos.sl_locked and pos.lock_trigger_roe > 0:
                if pos.max_fav_roe >= pos.lock_trigger_roe:
                    new_sl = pos.entry_price
                    if self.executor.update_sl_order(
                            pos.symbol, pos.direction, pos.quantity, new_sl, pos):
                        pos.sl_locked = True
                        pos.stop_loss = new_sl
                        self._save_positions()
                        self.tg.send(f"{self.bot_tag} 🔒 SL 본전 잠금 | {pos.symbol}\n"
                                     f"현물+{pos.max_fav_roe:.1f}% → SL={new_sl:,.6f}")
                    else:
                        # 실패 시 sl_locked=False 유지 → 다음 루프 재시도
                        self.tg.send(
                            f"{self.bot_tag} ⚠️ SL 잠금 실패 (재시도 예정) | {pos.symbol}\n"
                            f"→ 15초 후 자동 재시도")

            # Trail (D모드)
            trail_pct = self.cfg["TRAIL_PCT"]
            if trail_pct > 0 and pos.sl_locked and pos.max_fav_roe > 0.5:
                trail_threshold = pos.max_fav_roe * (1 - trail_pct / 100)
                if roe_spot <= trail_threshold:
                    self._close(pos, price, f'TRAIL_{trail_pct}%', closed); continue

            # EE
            if (self.cfg["ENABLE_EARLY_EXIT"] and not pos.ee_checked
                    and hc >= self.cfg["EE_START_CANDLE"]):
                if hc <= self.cfg["EE_END_CANDLE"]:
                    if roe_lev <= self.cfg["EE_THRESHOLD_ROE"]:
                        self._close(pos, price, f'EE_{hc//4}h', closed); continue
                else:
                    pos.ee_checked = True

            # Timeout
            if hh >= self.cfg["TIMEOUT_H"]:
                self._close(pos, price, 'TO', closed); continue

        for p in closed:
            if p in self.positions: self.positions.remove(p)
        if closed: self._save_positions()

    def _close(self, pos, price, reason, closed_list):
        result = self.executor.close_position(pos.symbol, pos.direction,
                                               pos.quantity, price, reason)
        if not result:
            time.sleep(1)
            price = self.executor.price(pos.symbol) or price
            result = self.executor.close_position(pos.symbol, pos.direction,
                                                   pos.quantity, price, reason)
        if not result: return

        ap = result['price']
        pnl = pos.pnl(ap) - result['fee']
        roe = pos.current_roe(ap)
        hh = pos.hold_h()
        self.daily_pnl += pnl

        if self.cfg["PAPER_TRADE"]:
            mg_return = pos.quantity * pos.entry_price / self.cfg["LEVERAGE"]
            self.executor.paper_bal += pnl + mg_return

        # 히스토리 저장
        self._save_history({
            'bot': 'bot2', 'symbol': pos.symbol, 'direction': pos.direction,
            'entry_price': pos.entry_price, 'exit_price': ap,
            'pnl': round(pnl, 4), 'roe_pct': round(roe, 2),
            'hold_h': round(hh, 1), 'reason': reason,
            'sl_locked': pos.sl_locked, 'confidence': pos.confidence,
            'adx': pos.adx_value, 'vol_ratio': pos.volume_ratio,
            'gap_pct': pos.gap_pct, 'vol_surge': pos.vol_surge,
            'squeeze_candles': pos.squeeze_candles,
            'time': datetime.now(KST).isoformat(),
        })

        icon = "🟢 수익" if pnl > 0 else "🔴 손실"
        lock_s = " [본전잠금]" if pos.sl_locked else ""
        roe_lev = roe * self.cfg["LEVERAGE"]
        bal = self.executor.total_balance()
        msg = (f"{self.bot_tag} {icon} | {pos.direction.upper()} {pos.symbol} | {reason}{lock_s}\n"
               f"━━━━━━━━━━━━━━━━\n"
               f"진입: {pos.entry_price:,.6f} → 청산: {ap:,.6f}\n"
               f"손익: ${pnl:+,.2f} (현물{roe:+.2f}% ROE{roe_lev:+.0f}%)\n"
               f"보유: {hh:.1f}시간\n"
               f"━━━━━━━━━━━━━━━━\n"
               f"📋 진입 시그널:\n"
               f"  conf:{pos.confidence} GAP:{pos.gap_pct:.2f}% sqz:{pos.squeeze_candles}\n"
               f"  ADX:{pos.adx_value:.0f} vol:×{pos.volume_ratio:.1f} surge:×{pos.vol_surge:.1f}\n"
               f"━━━━━━━━━━━━━━━━\n"
               f"오늘: ${self.daily_pnl:+,.2f} | 잔고: ${bal:,.0f}")
        self.tg.send(msg)
        closed_list.append(pos)
        self._alerted_orders.discard(f"{pos.symbol}_{pos.direction}")

        # [Phase D] 에이전트 — 청산 결과 기록 + 반성
        if self.agent_hook:
            try:
                self.agent_hook.on_position_close(pos, reason, roe)
            except Exception:
                pass

    def _save_history(self, record):
        p = os.path.join(self.cfg["LOG_DIR"], "trade_history_v9_bot2.jsonl")
        with open(p, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    @staticmethod
    def _norm_sym(sym):
        s = str(sym).split(':')[0]
        if '/' not in s and s.endswith('USDT'):
            s = s[:-4] + '/USDT'
        return s

    def runtime_sync(self):
        """5분마다 거래소 실제 포지션과 비교 — SL/TP 자동 체결 감지"""
        if self.cfg["PAPER_TRADE"] or not self.positions:
            return
        try:
            ex_positions = self.exchange.fetch_positions()
            ex_keys = set()
            for p in ex_positions:
                contracts = abs(float(p.get('contracts', 0)))
                if contracts <= 0: continue
                sym = self._norm_sym(p.get('symbol', ''))
                side = p.get('side', '').lower()
                ex_keys.add(f"{sym}_{side}")

            removed = []
            for pos in self.positions:
                key = f"{self._norm_sym(pos.symbol)}_{pos.direction}"
                if key not in ex_keys:
                    price = self.executor.price(pos.symbol)
                    if price <= 0: price = pos.entry_price
                    pnl = pos.pnl(price)
                    roe = pos.current_roe(price)
                    hh = pos.hold_h()
                    self.daily_pnl += pnl

                    self._save_history({
                        'bot': 'bot2', 'symbol': pos.symbol, 'direction': pos.direction,
                        'entry_price': pos.entry_price, 'exit_price': price,
                        'pnl': round(pnl, 4), 'roe_pct': round(roe, 2),
                        'hold_h': round(hh, 1), 'reason': 'EXCHANGE_AUTO',
                        'sl_locked': pos.sl_locked, 'confidence': pos.confidence,
                        'time': datetime.now(KST).isoformat(),
                    })

                    icon = "🟢" if pnl > 0 else "🔴"
                    roe_lev = roe * self.cfg["LEVERAGE"]
                    bal = self.executor.total_balance()
                    self.tg.send(
                        f"{self.bot_tag} {icon} 거래소 자동 청산 | {pos.direction.upper()} {pos.symbol}\n"
                        f"추정 손익: ${pnl:+,.2f} (현물{roe:+.2f}%)\n"
                        f"보유: {hh:.1f}시간 | 잔고: ${bal:,.0f}")
                    # 잔여 주문 정리 (TP or SL 남아있을 수 있음)
                    try:
                        self.executor._cancel_all_orders(pos.symbol)
                    except: pass
                    removed.append(pos)
                    self._alerted_orders.discard(f"{pos.symbol}_{pos.direction}")

            for p in removed:
                if p in self.positions: self.positions.remove(p)
            if removed:
                self._save_positions()
                print(f"  {self.bot_tag} 동기화: {len(removed)}개 거래소 청산 감지")
        except Exception as e:
            print(f"  {self.bot_tag} 동기화 실패: {e}")

    def verify_all_orders(self):
        """30분마다 SL/TP 주문 존재 확인"""
        for pos in self.positions:
            key = f"{pos.symbol}_{pos.direction}"
            if key in self._alerted_orders: continue
            check = self.executor.verify_sl_tp(pos.symbol, pos.direction)
            if check['sl'] is None or check['tp'] is None: continue
            missing = []
            if not check['sl']: missing.append('SL')
            if not check['tp']: missing.append('TP')
            if missing:
                self.tg.send_error(f"{self.bot_tag} {pos.symbol} {'+'.join(missing)} 주문 없음!")
                self._alerted_orders.add(key)

    # ── 저장/로드 ──

    def _save_positions(self):
        data = []
        for p in self.positions:
            d = {}
            for k, v in p.__dict__.items():
                d[k] = {str(kk): vv for kk, vv in v.items()} if isinstance(v, dict) else v
            data.append(d)
        path = self.cfg["POSITIONS_FILE"]
        if os.path.exists(path):
            try: os.replace(path, path + '.bak')
            except: pass
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, default=str, ensure_ascii=False)

    def _load_positions(self):
        for try_path in [self.cfg["POSITIONS_FILE"], self.cfg["POSITIONS_FILE"] + '.bak']:
            if not os.path.exists(try_path): continue
            try:
                with open(try_path, encoding='utf-8') as f: data = json.load(f)
                for d in data:
                    for df in ['roe_history']:
                        raw = d.get(df, {})
                        d[df] = {(int(k) if k.isdigit() else k): v for k, v in raw.items()}
                    reasons = d.pop('reasons', [])
                    valid = {f.name for f in Position.__dataclass_fields__.values()}
                    clean = {k: v for k, v in d.items() if k in valid}
                    clean['reasons'] = reasons
                    self.positions.append(Position(**clean))
                if self.positions:
                    print(f"  {self.bot_tag} 포지션 복원: {len(self.positions)}개")
                return
            except Exception as e:
                print(f"  {self.bot_tag} 포지션 로드 실패: {e}")
        self.positions = []

    # ── 메인 루프 ──

    def run(self):
        print(f"\n{'='*50}")
        print(f"  {self.bot_tag} Hunt Trader [모드{self.mode}]")
        print(f"{'='*50}")

        last_scan = 0; last_status = 0; last_bar_min = -1
        last_verify = 0; last_sync = 0

        while True:
            try:
                now = time.time()
                now_dt = datetime.now(KST)

                # ── BTC 상태 업데이트 ──
                prev_state = self.hunt_tracker.state
                new_state = self.hunt_tracker.update()

                # [Phase D] 급락/추세 감지 (매 루프) — periodic_check 제거 (/b2판결로 대체)
                if self.agent_hook:
                    try:
                        self.agent_hook.crash_check(self.positions)
                    except Exception:
                        pass
                    try:
                        self.agent_hook.trend_check(self.positions)
                    except Exception:
                        pass

                # [Phase F] 텔레그램 명령어 수신 (매 루프)
                try:
                    self._process_telegram_commands()
                except Exception:
                    pass

                # 상태 전환 알림
                if new_state != prev_state:
                    if new_state == BTCBreakoutTracker.HUNT:
                        d = self.hunt_tracker.hunt_direction
                        self.tg.send(
                            f"{self.bot_tag} 🎯 HUNT 시작!\n"
                            f"BTC {d} 돌파 감지 → {self.cfg['HUNT_WINDOW_HOURS']}h 알트 스캔\n"
                            f"BTC: {self.btc_trend.status_str()}")
                    elif new_state == BTCBreakoutTracker.COOLDOWN:
                        self.tg.send(f"{self.bot_tag} ⏸️ HUNT 종료 → cooldown")
                    elif new_state == BTCBreakoutTracker.STANDBY:
                        self.tg.send(f"{self.bot_tag} 💤 standby (BTC squeeze 대기)")

                # ── 포지션 관리 (항상) ──
                if self.positions:
                    self.manage_positions()

                # ── hunt 모드에서만 스캔 ──
                if self.hunt_tracker.is_hunting:
                    now_dt = datetime.now(KST)
                    cur_min = now_dt.minute
                    bar_boundary = cur_min % 15 == 0 and now_dt.second < 45

                    if bar_boundary and cur_min != last_bar_min:
                        for sig in self.scan():
                            self.try_enter(sig)
                        last_scan = now; last_bar_min = cur_min
                    elif now - last_scan >= self.cfg["SCAN_SEC"]:
                        for sig in self.scan():
                            self.try_enter(sig)
                        last_scan = now

                # ── 상태 보고 (2시간) ──
                if now - last_status >= self.cfg["STATUS_SEC"]:
                    self._status_report()
                    last_status = now

                # ── SL/TP 검증 (30분) ──
                if now - last_verify >= 1800 and self.positions:
                    self.verify_all_orders()
                    last_verify = now

                # ── 거래소 동기화 (5분) — SL/TP 자동 청산 감지 ──
                if now - last_sync >= 300 and self.positions:
                    self.runtime_sync()
                    last_sync = now

                # ── 일일 리셋 ──
                if now_dt.hour == 0 and not self._daily_reset_done:
                    if self.daily_trades > 0:
                        self.tg.send(f"{self.bot_tag} 📅 일일결산\n"
                                     f"거래: {self.daily_trades}건\n"
                                     f"손익: ${self.daily_pnl:+,.2f}")
                    self.daily_trades = 0; self.daily_pnl = 0
                    self._daily_reset_done = True
                elif now_dt.hour != 0:
                    self._daily_reset_done = False

                time.sleep(self.cfg["MONITOR_SEC"])

            except KeyboardInterrupt:
                self.tg.send(f"{self.bot_tag} 🛑 수동 종료"); break
            except Exception as e:
                traceback.print_exc()
                self.tg.send_error(f"{self.bot_tag} 오류: {e}")
                time.sleep(60)

    # ── [Phase F] 텔레그램 명령어 처리 ──

    def _process_telegram_commands(self):
        try:
            updates = self.tg.get_updates()
        except Exception:
            return
        for update in updates:
            msg = update.get("message", {})
            text = msg.get("text", "").strip()
            if not text:
                continue
            try:
                self._handle_command(text)
            except Exception as e:
                print(f"  {self.bot_tag} [TG 명령] 처리 오류: {e}")

    def _handle_command(self, text: str):
        text_lower = text.lower().strip()

        if text_lower in ("/help", "/명령어", "/도움", "help", "도움", "명령어"):
            self.tg.send(
                f"{self.bot_tag} 📋 사용 가능한 명령어\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "/b2판결 — 전체 보유 포지션 판결\n"
                "/b2분석 — 전체 시장+포지션 분석\n"
                "/b2포지션 <코인> — 개별 포지션 분석\n"
                "/b2진입 <코인> — 진입 시그널 판단\n"
                "/b2시장 — 즉시 시장 분석\n"
                "/b2상태 — 봇 상태 요약\n"
                "/b2메모리 — Brain 메모리 통계\n"
                "/<코인>분석 — 알트코인 구조 분석\n"
                "  예: /sol분석, /eth분석\n"
                "/b2help — 이 도움말"
            )
            return

        # /<코인>분석 — 알트코인 구조 분석
        import re
        alt_match = re.match(r'^/([a-zA-Z]+)분석$', text_lower.strip())
        if alt_match:
            coin = alt_match.group(1).upper()
            if coin in ("BTC", "BITCOIN"):
                self.tg.send(f"{self.bot_tag} ℹ️ BTC 분석은 /분석 또는 /시장 명령어를 사용하세요.")
                return
            symbol = f"{coin}/USDT"
            self.tg.send(f"{self.bot_tag} 🔍 {coin} 구조 분석 중...")

            def _run():
                try:
                    from agents.analyzers.alt_structure import AltStructureAgent
                    agent = AltStructureAgent(symbol=symbol, exchange=self.exchange)
                    data = agent.collect_data()
                    result = agent.analyze(data)
                    try:
                        chart_png = agent.generate_chart(data, result)
                        caption = agent.format_telegram(result)
                        self.tg.send_photo(chart_png, caption=caption[:1024])
                    except Exception as chart_err:
                        print(f"  차트 생성 실패, 텍스트 전송: {chart_err}")
                        msg = agent.format_telegram(result)
                        for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
                            self.tg.send(chunk)
                except Exception as e:
                    self.tg.send(f"{self.bot_tag} ❌ {coin} 분석 실패: {e}")

            threading.Thread(target=_run, daemon=True).start()
            return

        # /판결 — 전체 보유 포지션 판결
        if text_lower in ("/판결", "/judge"):
            if not self.agent_hook:
                self.tg.send(f"{self.bot_tag} ❌ 에이전트 시스템 비활성화")
                return
            if not self.positions:
                self.tg.send(f"{self.bot_tag} 📋 보유 포지션 없음")
                return
            n = len(self.positions)
            syms = ", ".join(p.symbol.replace('/USDT', '') for p in self.positions)
            self.tg.send(f"{self.bot_tag} ⚖️ 전체 포지션 판결 중... ({n}건: {syms})")

            def _run():
                try:
                    pos_infos = self.agent_hook._convert_positions(self.positions)
                    orch = self.agent_hook._get_orchestrator()
                    consensus = orch.run(positions=pos_infos)
                    self.agent_hook._memory.store(consensus)
                    messages = self.agent_hook._formatter.format_consensus(consensus)
                    header = f"{self.bot_tag} ⚖️ 포지션 판결 ({n}건)\n{'━' * 25}\n"
                    full = header + "\n".join(messages)
                    for chunk in [full[i:i+4000] for i in range(0, len(full), 4000)]:
                        self.tg.send(chunk)
                except Exception as e:
                    self.tg.send(f"{self.bot_tag} ❌ 판결 실패: {e}")

            threading.Thread(target=_run, daemon=True).start()
            return

        # /분석 — 전체 시장+포지션 분석
        if text_lower in ("/분석", "/analyze"):
            if not self.agent_hook:
                self.tg.send(f"{self.bot_tag} ❌ 에이전트 시스템 비활성화")
                return
            n = len(self.positions)
            self.tg.send(f"{self.bot_tag} 🔍 전체 시장 분석 중... (포지션 {n}건)")

            def _run():
                try:
                    pos_infos = self.agent_hook._convert_positions(self.positions) if self.positions else []
                    orch = self.agent_hook._get_orchestrator()
                    consensus = orch.run(positions=pos_infos)
                    self.agent_hook._memory.store(consensus)
                    self.agent_hook._send_result(consensus, trigger="분석요청")
                except Exception as e:
                    self.tg.send(f"{self.bot_tag} ❌ 분석 실패: {e}")

            threading.Thread(target=_run, daemon=True).start()
            return

        if text_lower.startswith(("/포지션", "/position")):
            symbol = self._parse_symbol(text)
            if not symbol:
                self.tg.send("❌ 사용법: /포지션 <코인>\n예: /포지션 ETH")
                return
            if not self.agent_hook:
                self.tg.send("❌ 에이전트 시스템 비활성화")
                return
            self.tg.send(f"{self.bot_tag} 🔍 {symbol} 포지션 분석 중...")

            def _run():
                result = self.agent_hook.analyze_position(symbol, self.positions)
                self.tg.send(result[:4000])

            threading.Thread(target=_run, daemon=True).start()
            return

        if text_lower.startswith(("/진입", "/entry")):
            symbol = self._parse_symbol(text)
            if not symbol:
                self.tg.send("❌ 사용법: /진입 <코인>\n예: /진입 SOL")
                return
            if not self.agent_hook:
                self.tg.send("❌ 에이전트 시스템 비활성화")
                return
            self.tg.send(f"{self.bot_tag} 🔍 {symbol} 진입 분석 중...")

            def _run():
                result = self.agent_hook.analyze_entry(symbol, exchange=self.exchange)
                self.tg.send(result[:4000])

            threading.Thread(target=_run, daemon=True).start()
            return

        if text_lower in ("/시장", "/market"):
            if not self.agent_hook:
                self.tg.send("❌ 에이전트 시스템 비활성화")
                return
            self.tg.send(f"{self.bot_tag} 🔍 시장 분석 중...")

            def _run():
                result = self.agent_hook.analyze_market_now(self.positions)
                for chunk in [result[i:i+4000] for i in range(0, len(result), 4000)]:
                    self.tg.send(chunk)

            threading.Thread(target=_run, daemon=True).start()
            return

        if text_lower in ("/상태", "/status"):
            self._status_report()
            return

        if text_lower in ("/메모리", "/memory"):
            if not self.agent_hook:
                self.tg.send("❌ 에이전트 시스템 비활성화")
                return
            stats = self.agent_hook.get_memory_stats()
            if not stats:
                self.tg.send("❌ 메모리 시스템 비활성화")
                return
            brain = stats.get("brain", {})
            reflection = stats.get("reflection", {})
            rules = brain.get("rules", {})
            lines = [
                f"{self.bot_tag} 🧠 메모리 통계",
                f"{'━' * 20}",
                f"규칙: {rules.get('total_rules', 0)}개 (활성 {rules.get('active_rules', 0)}개)",
                f"평균 적중률: {rules.get('avg_hit_rate', 0):.0%}",
                f"코인 추적: {brain.get('coins_tracked', 0)}개",
                f"패턴 추적: {brain.get('patterns_tracked', 0)}개",
                f"반성 횟수: {reflection.get('reflection_count', 0)}회",
            ]
            top_coins = brain.get("top_coins", [])
            if top_coins:
                lines.append(f"\n📊 상위 코인:")
                for c in top_coins[:5]:
                    lines.append(f"  {c['symbol']}: 승률 {c['winrate']:.0%} ({c['trades']}건)")
            self.tg.send("\n".join(lines))
            return

    def _parse_symbol(self, text: str) -> str:
        parts = text.strip().split()
        if len(parts) < 2:
            return ""
        coin = parts[1].upper().replace("/USDT", "").replace("USDT", "")
        if not coin:
            return ""
        symbol = f"{coin}/USDT"
        if symbol not in self.cfg.get("WATCHLIST", []):
            similar = [s for s in self.cfg.get("WATCHLIST", []) if coin in s]
            if similar:
                symbol = similar[0]
            else:
                self.tg.send(f"⚠️ {coin} — 워치리스트에 없음. {symbol}로 시도합니다.")
        return symbol

    def _status_report(self):
        bal = self.executor.total_balance()
        bot1_margin = self._get_bot1_margin()
        hunt_s = self.hunt_tracker.status_str()
        now = datetime.now(KST).strftime('%m/%d %H:%M')

        lines = [f"{self.bot_tag} 📊 상태 [{now}]",
                 f"잔고: ${bal:,.0f} | Bot1마진: ${bot1_margin:,.0f}",
                 f"BTC: {hunt_s}"]

        if self.positions:
            lines.append(f"\n포지션: {len(self.positions)}/{self.cfg['MAX_POSITIONS']}")
            for p in self.positions:
                pr = self.executor.price(p.symbol)
                if pr <= 0: continue
                r = p.current_roe(pr)
                pnl = p.pnl(pr)
                hh = p.hold_h()
                d_s = 'L' if p.direction == 'long' else 'S'
                lock_s = "🔒" if p.sl_locked else "🔓"
                lines.append(f"  {d_s} {p.symbol} {lock_s} 현물{r:+.2f}% ${pnl:+,.1f} {hh:.0f}h")

        if self.daily_pnl != 0:
            lines.append(f"\n오늘: ${self.daily_pnl:+,.2f} ({self.daily_trades}건)")

        msg = '\n'.join(lines)
        self.tg.send(msg)
        print(msg)


# ═══════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Bot2: BTC Breakout Hunt')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--mode', default='E', choices=['E', 'D'])
    parser.add_argument('--hunt-hours', type=float, default=3,
                        help='Hunt window duration (hours)')
    parser.add_argument('--profile', default='standard')
    args = parser.parse_args()

    cfg = CONFIG_BOT2.copy()
    if args.live:
        cfg["PAPER_TRADE"] = False
    cfg["_mode"] = args.mode
    cfg["_profile"] = args.profile
    cfg["HUNT_WINDOW_HOURS"] = args.hunt_hours

    HuntTrader(cfg).run()
