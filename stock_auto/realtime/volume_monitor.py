"""
개장 거래량 폭주 감지 · 알람 모듈 (Stage 7)

설계 문서 §4 방법론 구현:
  - (B) 거래량 축:  시간대 RVOL,  거래량 Z-score
  - (A) 가격 축:    시가 갭,  분당 ROC,  틱 빈도
  - 노이즈 제거:    거래대금 하한
  - 방향성:         체결강도(매수/매도 체결 비율)
  - 결합:           (B) AND (A) AND 거래대금  [AND 체결강도(선택)]
  - 운영:           멀티 윈도우(1·3·5분), 쿨다운/디듀프

핵심 원칙 — "시간대 정규화":
  개장 직후 거래량은 원래 높다. 따라서 일평균이 아니라
  '같은 시간대(개장 후 경과분)의 과거 누적 거래량'과 비교한다.

KIS Open API 미발급 상태이므로 데이터 피드를 추상화(DataFeed)했다.
지금은 ReplayFeed로 로직을 검증하고, 발급 후 KISFeed 어댑터만 끼우면 된다.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Iterable, Iterator, Optional, Protocol


# ──────────────────────────────────────────────────────────────────────────
# 1. 시장 구분 · 임계값 설정
# ──────────────────────────────────────────────────────────────────────────
class Market(str, Enum):
    KR = "KR"   # KOSPI / KOSDAQ
    US = "US"   # NASDAQ / NYSE


@dataclass(frozen=True)
class SurgeThresholds:
    """시장별 폭주 판정 임계값. 백테스트 전 '시작값'이며 운영하며 튜닝한다."""
    # (B) 거래량 — 둘 중 하나만 충족해도 거래량 조건 통과(OR)
    rvol_min: float = 3.0           # 시간대 RVOL 하한
    zscore_min: float = 3.0         # 시간대 거래량 Z-score 하한
    # (A) 가격 — 갭 또는 분당 ROC 중 큰 값으로 평가(OR)
    price_move_min_pct: float = 3.0  # |시가갭| 또는 |분당ROC| 하한 (%)
    # 노이즈 제거 — 절대 거래대금 하한 (시장 통화 기준)
    turnover_floor: float = 5_000_000_000  # KR: 50억 KRW
    # 방향성(선택) — 매수 우위 확인. None이면 미적용
    strength_min: Optional[float] = 1.2      # 체결강도(매수체결/매도체결)
    # 틱 빈도(선택) — 단위시간 체결 건수 급증. None이면 미적용
    tick_rate_min: Optional[float] = None    # 초당 체결 건수 하한


# 시장별 기본 임계값 (설계 §4.3 시작값)
DEFAULT_THRESHOLDS: dict[Market, SurgeThresholds] = {
    Market.KR: SurgeThresholds(
        rvol_min=3.0, zscore_min=3.0,
        price_move_min_pct=3.0,
        turnover_floor=5_000_000_000,   # 50억 원
        strength_min=1.2,
    ),
    Market.US: SurgeThresholds(
        rvol_min=3.0, zscore_min=3.0,
        price_move_min_pct=5.0,          # 미국은 변동성 커서 더 높게
        turnover_floor=2_000_000,        # $2M
        strength_min=1.2,
    ),
}

# 멀티 윈도우 — 개장 후 평가 시점(분). 너무 짧으면 동시호가 잡음, 길면 늦음.
EVAL_WINDOWS_MIN: tuple[int, ...] = (1, 3, 5)

# 쿨다운 — 같은 종목 재알림 억제(초)
DEDUP_COOLDOWN_SEC: int = 600


# ──────────────────────────────────────────────────────────────────────────
# 2. 데이터 구조
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class TickSnapshot:
    """피드가 종목당 1건씩 내보내는 실시간 스냅샷."""
    symbol: str
    market: Market
    ts: datetime                 # 현재 시각
    price: float                 # 현재가
    prev_close: float            # 전일 종가 (갭 계산용)
    open_price: float            # 당일 시가
    session_open: datetime       # 당일 개장 시각 (경과분 = ts - session_open)
    cum_volume: float            # 개장 후 누적 거래량(주)
    cum_turnover: float          # 개장 후 누적 거래대금(통화)
    buy_exec_volume: float = 0.0   # 매수 체결량 (체결강도 계산용)
    sell_exec_volume: float = 0.0  # 매도 체결량
    trade_count: int = 0           # 누적 체결 건수 (틱 빈도용)

    @property
    def elapsed_min(self) -> float:
        return (self.ts - self.session_open).total_seconds() / 60.0

    @property
    def gap_pct(self) -> float:
        if self.prev_close <= 0:
            return 0.0
        return (self.price - self.prev_close) / self.prev_close * 100.0

    @property
    def strength(self) -> Optional[float]:
        """체결강도 = 매수체결 / 매도체결. 매도체결 0이면 None."""
        if self.sell_exec_volume <= 0:
            return None
        return self.buy_exec_volume / self.sell_exec_volume


@dataclass
class HistoricalProfile:
    """
    종목별 '시간대 누적 거래량' 분포(과거 N일).
    minute_buckets[m] = (평균 누적거래량, 표준편차) — 개장 후 m분 시점 기준.
    build_from_intraday()로 일별 분봉에서 생성한다.
    """
    symbol: str
    # 분 버킷 → (mean, std)
    buckets: dict[int, tuple[float, float]] = field(default_factory=dict)

    def lookup(self, elapsed_min: float) -> Optional[tuple[float, float]]:
        """경과분에 가장 가까운(이하) 버킷의 (mean, std) 반환."""
        if not self.buckets:
            return None
        m = int(elapsed_min)
        # 정확 버킷 없으면 그 이하 최댓값 버킷 사용
        candidates = [b for b in self.buckets if b <= m]
        key = max(candidates) if candidates else min(self.buckets)
        return self.buckets[key]


# ──────────────────────────────────────────────────────────────────────────
# 3. 지표 계산 + 결합 판정 (순수 함수 / 검증 용이)
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class SurgeMetrics:
    rvol: Optional[float]
    zscore: Optional[float]
    price_move_pct: float        # max(|gap|, |roc_1m|)
    turnover: float
    strength: Optional[float]
    tick_rate: Optional[float]   # 초당 체결 건수
    roc_1m_pct: float


def compute_metrics(
    snap: TickSnapshot,
    profile: Optional[HistoricalProfile],
    price_1m_ago: Optional[float],
    trade_count_1m_ago: Optional[int],
    secs_since_prev_count: Optional[float],
) -> SurgeMetrics:
    """스냅샷 + 과거 프로파일 + 1분 전 상태로 지표를 계산한다."""
    # (B) 시간대 RVOL / Z-score
    rvol = zscore = None
    if profile is not None:
        stat = profile.lookup(snap.elapsed_min)
        if stat is not None:
            mean, std = stat
            if mean > 0:
                rvol = snap.cum_volume / mean
            if std > 0:
                zscore = (snap.cum_volume - mean) / std

    # (A) 분당 ROC
    roc_1m = 0.0
    if price_1m_ago and price_1m_ago > 0:
        roc_1m = (snap.price - price_1m_ago) / price_1m_ago * 100.0
    price_move = max(abs(snap.gap_pct), abs(roc_1m))

    # 틱 빈도(초당 체결 건수)
    tick_rate = None
    if (trade_count_1m_ago is not None and secs_since_prev_count
            and secs_since_prev_count > 0):
        tick_rate = (snap.trade_count - trade_count_1m_ago) / secs_since_prev_count

    return SurgeMetrics(
        rvol=rvol, zscore=zscore,
        price_move_pct=price_move,
        turnover=snap.cum_turnover,
        strength=snap.strength,
        tick_rate=tick_rate,
        roc_1m_pct=roc_1m,
    )


def is_surge(m: SurgeMetrics, th: SurgeThresholds) -> tuple[bool, list[str]]:
    """
    설계 §4.3 결합식:
        (RVOL≥ OR Z≥)  AND  가격이동≥  AND  거래대금≥  [AND 체결강도≥]  [AND 틱빈도≥]
    반환: (폭주여부, 충족 사유 리스트)
    """
    reasons: list[str] = []

    # (B) 거래량 — OR
    vol_ok = False
    if m.rvol is not None and m.rvol >= th.rvol_min:
        vol_ok = True
        reasons.append(f"RVOL={m.rvol:.1f}≥{th.rvol_min}")
    if m.zscore is not None and m.zscore >= th.zscore_min:
        vol_ok = True
        reasons.append(f"Z={m.zscore:.1f}≥{th.zscore_min}")
    if not vol_ok:
        return False, reasons

    # (A) 가격 이동 — AND
    if m.price_move_pct < th.price_move_min_pct:
        return False, reasons
    reasons.append(f"가격이동={m.price_move_pct:.1f}%≥{th.price_move_min_pct}%")

    # 거래대금 하한 — AND (노이즈 제거)
    if m.turnover < th.turnover_floor:
        return False, reasons
    reasons.append(f"거래대금={m.turnover:,.0f}≥{th.turnover_floor:,.0f}")

    # 체결강도 — 선택 AND
    if th.strength_min is not None:
        if m.strength is None or m.strength < th.strength_min:
            return False, reasons
        reasons.append(f"체결강도={m.strength:.2f}≥{th.strength_min}")

    # 틱 빈도 — 선택 AND
    if th.tick_rate_min is not None:
        if m.tick_rate is None or m.tick_rate < th.tick_rate_min:
            return False, reasons
        reasons.append(f"틱빈도={m.tick_rate:.1f}/s≥{th.tick_rate_min}")

    return True, reasons


# ──────────────────────────────────────────────────────────────────────────
# 4. 알람 신호 + 모니터 (상태 관리 · 쿨다운 · 콜백)
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class SurgeSignal:
    symbol: str
    market: Market
    ts: datetime
    price: float
    metrics: SurgeMetrics
    reasons: list[str]
    window_min: int

    def to_telegram(self) -> str:
        m = self.metrics
        rvol = f"{m.rvol:.1f}" if m.rvol is not None else "N/A"
        z = f"{m.zscore:.1f}" if m.zscore is not None else "N/A"
        stg = f"{m.strength:.2f}" if m.strength is not None else "N/A"
        return (
            f"🚨 [거래량 폭주] {self.symbol} ({self.market.value})\n"
            f"⏱️ 개장 {self.window_min}분 · {self.ts:%H:%M:%S}\n"
            f"💰 현재가 {self.price:,.2f} (갭 {m.price_move_pct:+.1f}%)\n"
            f"📊 RVOL {rvol} · Z {z} · 체결강도 {stg}\n"
            f"💵 거래대금 {m.turnover:,.0f}\n"
            f"✅ {' · '.join(self.reasons)}"
        )


class DataFeed(Protocol):
    """실시간 피드 인터페이스. KIS WebSocket 어댑터가 이를 구현한다."""
    def stream(self, symbols: Iterable[str]) -> Iterator[TickSnapshot]: ...


class SurgeMonitor:
    """
    여러 종목의 스냅샷 스트림을 받아 폭주를 감지하고 콜백으로 알린다.

    - 종목별 1분 전 가격·체결건수를 보관해 ROC·틱빈도 계산
    - 멀티 윈도우(1·3·5분)에서 윈도우당 1회만 알림
    - 쿨다운으로 중복 억제

    유니버스 정책(사용자 결정): 실시간 모니터는 '추천 종목 위주'로만 구독한다.
    """

    def __init__(
        self,
        profiles: dict[str, HistoricalProfile],
        thresholds: dict[Market, SurgeThresholds] = DEFAULT_THRESHOLDS,
        on_signal: Optional[Callable[[SurgeSignal], None]] = None,
        eval_windows: tuple[int, ...] = EVAL_WINDOWS_MIN,
        cooldown_sec: int = DEDUP_COOLDOWN_SEC,
        on_observation: Optional[Callable[[TickSnapshot, int], None]] = None,
    ):
        self.profiles = profiles
        self.thresholds = thresholds
        self.on_signal = on_signal or (lambda s: print(s.to_telegram()))
        self.eval_windows = eval_windows
        self.cooldown_sec = cooldown_sec
        # 자가보정 피드백: 각 윈도우에서 누적거래량 1회 기록(폭주 여부 무관)
        self.on_observation = on_observation

        # 종목별 롤링 상태: (시각, 가격, 체결건수) 1분 윈도우
        self._hist: dict[str, deque[tuple[datetime, float, int]]] = {}
        self._last_alert_ts: dict[str, float] = {}        # symbol → epoch
        self._fired_windows: dict[str, set[int]] = {}     # symbol → 발화한 윈도우
        self._observed_windows: dict[str, set[int]] = {}  # symbol → 기록한 윈도우

    def _rolling_ref(self, symbol: str, snap: TickSnapshot):
        """약 60초 전 (가격, 체결건수, 경과초)를 찾는다."""
        dq = self._hist.setdefault(symbol, deque())
        dq.append((snap.ts, snap.price, snap.trade_count))
        # 90초 넘는 항목 제거
        while dq and (snap.ts - dq[0][0]).total_seconds() > 90:
            dq.popleft()
        # 60초에 가장 가까운 과거 항목
        ref = None
        for t, p, c in dq:
            if (snap.ts - t).total_seconds() >= 55:
                ref = (t, p, c)
        if ref is None:
            return None, None, None
        secs = (snap.ts - ref[0]).total_seconds()
        return ref[1], ref[2], secs

    def _which_window(self, elapsed_min: float) -> Optional[int]:
        """현재 경과분이 평가 윈도우 근처(±0.5분)인지."""
        for w in self.eval_windows:
            if abs(elapsed_min - w) <= 0.5:
                return w
        return None

    def process(self, snap: TickSnapshot) -> Optional[SurgeSignal]:
        """스냅샷 1건 처리 → 폭주면 SurgeSignal 반환(+콜백)."""
        price_1m, count_1m, secs = self._rolling_ref(snap.symbol, snap)

        window = self._which_window(snap.elapsed_min)
        if window is None:
            return None
        # 관측 기록(자가보정): 폭주 여부와 무관하게 윈도우당 1회
        if (self.on_observation is not None
                and window not in self._observed_windows.get(snap.symbol, set())):
            self._observed_windows.setdefault(snap.symbol, set()).add(window)
            try:
                self.on_observation(snap, window)
            except Exception:  # noqa: BLE001 — 기록 실패가 모니터를 막지 않게
                pass
        # 이미 이 윈도우에서 발화했으면 skip
        if window in self._fired_windows.get(snap.symbol, set()):
            return None
        # 쿨다운
        last = self._last_alert_ts.get(snap.symbol)
        if last is not None and (time.time() - last) < self.cooldown_sec:
            return None

        metrics = compute_metrics(
            snap, self.profiles.get(snap.symbol),
            price_1m_ago=price_1m,
            trade_count_1m_ago=count_1m,
            secs_since_prev_count=secs,
        )
        th = self.thresholds[snap.market]
        surge, reasons = is_surge(metrics, th)
        if not surge:
            return None

        signal = SurgeSignal(
            symbol=snap.symbol, market=snap.market, ts=snap.ts,
            price=snap.price, metrics=metrics, reasons=reasons,
            window_min=window,
        )
        self._fired_windows.setdefault(snap.symbol, set()).add(window)
        self._last_alert_ts[snap.symbol] = time.time()
        self.on_signal(signal)
        return signal

    def run(self, feed: DataFeed, symbols: Iterable[str]) -> None:
        """피드 스트림을 소비하며 모니터링(블로킹)."""
        for snap in feed.stream(symbols):
            self.process(snap)


# ──────────────────────────────────────────────────────────────────────────
# 5. 과거 프로파일 빌더 (일별 분봉 → 시간대 누적 분포)
# ──────────────────────────────────────────────────────────────────────────
def build_profile_from_intraday(
    symbol: str,
    sessions: list[list[tuple[int, float]]],
    windows: Iterable[int] = range(1, 31),
) -> HistoricalProfile:
    """
    sessions: 과거 N일치. 각 일자는 [(개장후_경과분, 그 분까지 누적거래량), ...].
    windows:  프로파일을 만들 분 버킷들.
    각 버킷에서 일자별 누적거래량의 평균/표준편차를 계산한다.
    """
    buckets: dict[int, tuple[float, float]] = {}
    for w in windows:
        vals: list[float] = []
        for day in sessions:
            # 해당 일자에서 경과분 w 이하 최대 누적값
            cum = [v for (mn, v) in day if mn <= w]
            if cum:
                vals.append(max(cum))
        if len(vals) >= 2:
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
            buckets[w] = (mean, math.sqrt(var))
        elif vals:
            buckets[w] = (vals[0], 0.0)
    return HistoricalProfile(symbol=symbol, buckets=buckets)


# ──────────────────────────────────────────────────────────────────────────
# 6. 테스트용 리플레이 피드 (KIS 미발급 단계 검증용)
# ──────────────────────────────────────────────────────────────────────────
class ReplayFeed:
    """미리 만든 스냅샷 리스트를 그대로 흘려보내는 모의 피드."""
    def __init__(self, snapshots: list[TickSnapshot]):
        self._snaps = snapshots

    def stream(self, symbols: Iterable[str]) -> Iterator[TickSnapshot]:
        wanted = set(symbols)
        for s in self._snaps:
            if s.symbol in wanted:
                yield s


# KIS 어댑터 자리(발급 후 구현):
# class KISFeed:
#     """KIS Open API WebSocket(국내 H0STCNT0 / 해외 HDFSCNT0) → TickSnapshot 변환."""
#     def stream(self, symbols): ...  # TODO: 발급 후 구현


# ──────────────────────────────────────────────────────────────────────────
# 7. 데모 — 로직 검증 (python volume_monitor.py)
# ──────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from datetime import timedelta

    sym = "005930"
    open_t = datetime(2026, 6, 22, 9, 0, 0)

    # 과거 20일 가정: 개장 후 1/3/5분 누적거래량이 평균 10만/25만/40만 수준
    sessions = []
    for d in range(20):
        sessions.append([(1, 100_000 + d * 500),
                         (3, 250_000 + d * 800),
                         (5, 400_000 + d * 1000)])
    profile = build_profile_from_intraday(sym, sessions)
    print("프로파일:", profile.buckets)

    # 오늘: 개장 3분 누적거래량이 평소의 ~3.5배 + 가격 +4% 폭주 시나리오
    snaps = [
        # 1분차: 평소 수준 → 미발화
        TickSnapshot(sym, Market.KR, open_t + timedelta(minutes=1),
                     price=71000, prev_close=70000, open_price=70200,
                     session_open=open_t, cum_volume=110_000,
                     cum_turnover=7_700_000_000,
                     buy_exec_volume=60_000, sell_exec_volume=50_000,
                     trade_count=1200),
        # 3분차: 누적 90만(평소 25만의 3.6배) + 가격 +4% → 발화 기대
        TickSnapshot(sym, Market.KR, open_t + timedelta(minutes=3),
                     price=72800, prev_close=70000, open_price=70200,
                     session_open=open_t, cum_volume=900_000,
                     cum_turnover=65_000_000_000,
                     buy_exec_volume=600_000, sell_exec_volume=300_000,
                     trade_count=8000),
    ]

    monitor = SurgeMonitor({sym: profile}, DEFAULT_THRESHOLDS)
    monitor.run(ReplayFeed(snaps), [sym])
