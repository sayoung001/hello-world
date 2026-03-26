"""
SL 동적 조정 Gymnasium 환경
===========================
Phase 4: 144만 건 "방향 맞지만 SL 히트" 문제를 해결하기 위한 RL 환경.

에이전트가 각 시그널에 대해 SL 배수를 결정한다.
고정 SL 대신 시장 상태에 맞는 동적 SL을 학습.

관측 공간 (13차원):
    - atr_norm: 정규화된 ATR (변동성)
    - squeeze_range_norm: 스퀴즈 범위 / 가격
    - gap_pct: EMA 12/26 갭 %
    - confidence: 시그널 신뢰도 (0~100)
    - adx: ADX 값 (0~100)
    - volume_ratio: 거래량 / 20캔들 평균
    - rsi: RSI (0~100)
    - bb_width: 볼린저밴드 폭 / 가격
    - direction: 방향 (1=LONG, -1=SHORT)
    - hour_sin, hour_cos: 시간 사이클 인코딩
    - dow_sin, dow_cos: 요일 사이클 인코딩

행동 공간 (연속, 1차원):
    - SL 배수: [0.5, 3.0] — 기본 SL 거리에 곱하는 계수
      0.5 = 타이트한 SL, 3.0 = 넓은 SL

보상:
    - 동적 SL 적용 시 실현 ROE 기반
    - SL 히트 회피 + TP 도달 시 보너스
    - 과도하게 넓은 SL 페널티 (기회비용)
"""

from __future__ import annotations

import gymnasium as gym
import numpy as np
from gymnasium import spaces


# 레버리지 20x, 수수료 0.1% (편도)
LEVERAGE = 20
FEE_RATE = 0.001

# SL 배수 범위
SL_MULT_MIN = 0.5
SL_MULT_MAX = 3.0

# 타임아웃 캔들 수 (15분봉 기준, 24시간 = 96캔들)
TIMEOUT_CANDLES = 96

# 관측 차원
OBS_DIM = 13


class SLAdjustEnv(gym.Env):
    """
    에피소드-프리 환경: 각 스텝이 독립 시그널 1건.
    라벨링된 시그널 데이터를 순회하며, 에이전트가 SL 배수를 결정하면
    OHLC 시계열로 시뮬레이션하여 보상을 계산한다.

    Parameters
    ----------
    signals : np.ndarray
        shape (N, OBS_DIM) — 정규화된 관측 벡터
    ohlc_slices : list[np.ndarray]
        각 시그널의 진입 이후 OHLC (최대 TIMEOUT_CANDLES 행, 4열: O/H/L/C)
    base_sl_distances : np.ndarray
        shape (N,) — 기본(고정) SL 거리 (가격 단위, 양수)
    tp_distances : np.ndarray
        shape (N,) — TP 거리 (가격 단위, 양수)
    entry_prices : np.ndarray
        shape (N,) — 진입 가격
    directions : np.ndarray
        shape (N,) — 1=LONG, -1=SHORT
    shuffle : bool
        에포크마다 데이터 순서 셔플 여부
    """

    metadata = {"render_modes": []}

    def __init__(
        self,
        signals: np.ndarray,
        ohlc_slices: list[np.ndarray],
        base_sl_distances: np.ndarray,
        tp_distances: np.ndarray,
        entry_prices: np.ndarray,
        directions: np.ndarray,
        shuffle: bool = True,
    ):
        super().__init__()
        assert len(signals) == len(ohlc_slices) == len(base_sl_distances)
        self.signals = signals.astype(np.float32)
        self.ohlc_slices = ohlc_slices
        self.base_sl_distances = base_sl_distances.astype(np.float32)
        self.tp_distances = tp_distances.astype(np.float32)
        self.entry_prices = entry_prices.astype(np.float32)
        self.directions = directions.astype(np.float32)
        self.shuffle = shuffle
        self.n_signals = len(signals)

        # Gymnasium 인터페이스
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf, shape=(OBS_DIM,), dtype=np.float32
        )
        # SL 배수: [-1, 1] → 내부에서 [SL_MULT_MIN, SL_MULT_MAX]로 매핑
        self.action_space = spaces.Box(
            low=-1.0, high=1.0, shape=(1,), dtype=np.float32
        )

        self._idx = 0
        self._order: np.ndarray | None = None
        self._reset_order()

    def _reset_order(self):
        """데이터 순회 순서 초기화."""
        self._order = np.arange(self.n_signals)
        if self.shuffle:
            np.random.shuffle(self._order)
        self._idx = 0

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if self._idx >= self.n_signals:
            self._reset_order()
        obs = self.signals[self._order[self._idx]]
        return obs, {}

    def step(self, action: np.ndarray):
        # 액션 → SL 배수 매핑
        raw = float(np.clip(action[0], -1.0, 1.0))
        sl_mult = SL_MULT_MIN + (raw + 1.0) * 0.5 * (SL_MULT_MAX - SL_MULT_MIN)

        i = self._order[self._idx]
        entry = self.entry_prices[i]
        direction = self.directions[i]
        base_sl_dist = self.base_sl_distances[i]
        tp_dist = self.tp_distances[i]
        ohlc = self.ohlc_slices[i]  # (candles, 4)

        # 동적 SL/TP 가격 계산
        sl_dist = base_sl_dist * sl_mult
        if direction > 0:  # LONG
            sl_price = entry - sl_dist
            tp_price = entry + tp_dist
        else:  # SHORT
            sl_price = entry + sl_dist
            tp_price = entry - tp_dist

        # OHLC 시뮬레이션
        reward, info = self._simulate(ohlc, entry, sl_price, tp_price, direction)

        # 다음 시그널로 이동
        self._idx += 1
        terminated = True  # 각 시그널이 하나의 에피소드
        truncated = False

        # 다음 obs (에피소드 종료 후 reset에서 제공하지만, SB3 호환용)
        if self._idx < self.n_signals:
            next_obs = self.signals[self._order[self._idx]]
        else:
            next_obs = self.signals[self._order[0]]

        return next_obs, reward, terminated, truncated, info

    def _simulate(
        self,
        ohlc: np.ndarray,
        entry: float,
        sl_price: float,
        tp_price: float,
        direction: float,
    ) -> tuple[float, dict]:
        """
        OHLC 캔들을 순회하며 SL/TP 히트 여부 시뮬레이션.

        Returns
        -------
        reward : float
        info : dict
            exit_type, exit_candle, exit_price, roe
        """
        n_candles = len(ohlc)
        exit_type = "timeout"
        exit_price = ohlc[-1, 3] if n_candles > 0 else entry  # 타임아웃 시 마지막 close
        exit_candle = n_candles

        for c in range(n_candles):
            high = ohlc[c, 1]
            low = ohlc[c, 2]

            if direction > 0:  # LONG
                # SL 먼저 체크 (보수적: 같은 캔들에서 SL+TP 동시 가능 시 SL 우선)
                if low <= sl_price:
                    exit_type = "sl"
                    exit_price = sl_price
                    exit_candle = c
                    break
                if high >= tp_price:
                    exit_type = "tp"
                    exit_price = tp_price
                    exit_candle = c
                    break
            else:  # SHORT
                if high >= sl_price:
                    exit_type = "sl"
                    exit_price = sl_price
                    exit_candle = c
                    break
                if low <= tp_price:
                    exit_type = "tp"
                    exit_price = tp_price
                    exit_candle = c
                    break

        # ROE 계산 (20x 레버리지, 수수료 포함)
        if direction > 0:
            pnl_pct = (exit_price - entry) / entry
        else:
            pnl_pct = (entry - exit_price) / entry

        roe = pnl_pct * LEVERAGE - 2 * FEE_RATE * LEVERAGE  # 진입+청산 수수료

        # 보상 설계:
        # 1) 기본: 실현 ROE
        # 2) TP 도달 보너스 (+0.5)
        # 3) SL 히트 시 방향이 맞았다면 추가 페널티 (-0.3)
        #    → 방향 맞는데 SL 히트 = 에이전트가 SL을 충분히 넓히지 못한 것
        # 4) 홀딩 비용: 캔들당 -0.001 (과도하게 넓은 SL로 오래 홀딩 방지)
        reward = roe

        if exit_type == "tp":
            reward += 0.5
        elif exit_type == "sl":
            # 방향이 맞았는지 체크 (마지막 캔들 close 기준)
            if n_candles > 0:
                final_close = ohlc[-1, 3]
                direction_correct = (direction > 0 and final_close > entry) or (
                    direction < 0 and final_close < entry
                )
                if direction_correct:
                    reward -= 0.3  # 방향 맞는데 SL 히트 페널티

        # 홀딩 비용
        reward -= exit_candle * 0.001

        info = {
            "exit_type": exit_type,
            "exit_candle": exit_candle,
            "exit_price": exit_price,
            "roe": roe,
            "sl_mult": SL_MULT_MIN + (float(np.clip(roe, -1, 1)) + 1) * 0.5 * (SL_MULT_MAX - SL_MULT_MIN),
        }
        return float(reward), info
