"""
SL Agent — 동적 스탑로스 RL 에이전트 (Phase 4)
===============================================
핵심 문제: 370만 시그널 중 SL 히트 48.6%, 그 중 80.1%가 방향은 맞았음.
         → 144만 건의 "방향 맞지만 SL 히트" 해결이 목표.

접근법:
    PPO 에이전트가 시장 상태를 관측하고, 기본 SL 거리에 대한 배수(0.5x~3.0x)를
    출력하여 시그널별 최적 SL을 학습한다.

핵심 설계:
    1. 환경: SLAdjustEnv (rl/env/sl_env.py) — 시그널 1건 = 에피소드 1건
    2. 관측: 13차원 (ATR, 스퀴즈, GAP, ADX, RSI, BB폭, 시간 등)
    3. 행동: 연속 SL 배수 [0.5, 3.0]
    4. 보상: ROE + TP 보너스 - SL+방향맞음 페널티 - 홀딩비용
    5. 검증: Walk-Forward 3윈도우 (XGBoost 과적합 교훈 반영)

사용법:
    # 학습
    python -m rl.agent.sl_agent train --signals path/to/signals.csv --ohlcv path/to/15m/

    # 평가
    python -m rl.agent.sl_agent eval --model path/to/model.zip --signals path/to/signals.csv

    # 추론 (프로덕션)
    from rl.agent.sl_agent import SLAgent
    agent = SLAgent.load("experiments/sl_agent/best_model.zip")
    sl_mult = agent.predict(observation)
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Walk-Forward 윈도우 정의 ──────────────────────────────────────────
# XGBoost 과적합 교훈: 반드시 3윈도우 Walk-Forward 검증
WALK_FORWARD_WINDOWS = [
    {
        "name": "Window1",
        "train_end": "2025-03-31",
        "val_end": "2025-06-30",
        "test_end": "2025-09-30",
    },
    {
        "name": "Window2",
        "train_end": "2025-06-30",
        "val_end": "2025-09-30",
        "test_end": "2025-12-31",
    },
    {
        "name": "Window3",
        "train_end": "2025-09-30",
        "val_end": "2025-12-31",
        "test_end": "2026-03-31",
    },
]


@dataclass
class TrainConfig:
    """학습 하이퍼파라미터."""

    # PPO
    learning_rate: float = 3e-4
    n_steps: int = 2048
    batch_size: int = 256
    n_epochs: int = 10
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_range: float = 0.2
    ent_coef: float = 0.01
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5

    # 네트워크 구조
    net_arch: list = field(default_factory=lambda: [128, 64])

    # 학습 총 스텝
    total_timesteps: int = 500_000

    # 경로
    output_dir: str = "experiments/sl_agent"
    log_interval: int = 10


@dataclass
class EvalResult:
    """평가 결과 요약."""

    window_name: str
    split: str  # "val" or "test"
    n_signals: int
    mean_roe: float
    mean_roe_baseline: float  # 고정 SL 기준
    roe_improvement: float
    tp_rate: float
    sl_rate: float
    timeout_rate: float
    mean_sl_mult: float
    profit_factor: float
    profit_factor_baseline: float


class SLAgent:
    """
    동적 SL 에이전트.
    stable-baselines3 PPO를 래핑하여 학습/추론/평가 인터페이스 제공.
    """

    def __init__(self, model=None, config: TrainConfig | None = None):
        self.model = model
        self.config = config or TrainConfig()

    @classmethod
    def load(cls, model_path: str | Path) -> SLAgent:
        """저장된 모델 로드."""
        from stable_baselines3 import PPO

        model = PPO.load(str(model_path))
        return cls(model=model)

    def predict(self, observation: np.ndarray) -> float:
        """
        단일 관측에 대한 SL 배수 예측.

        Parameters
        ----------
        observation : np.ndarray
            shape (13,) 관측 벡터

        Returns
        -------
        sl_mult : float
            SL 배수 [0.5, 3.0]
        """
        from rl.env.sl_env import SL_MULT_MAX, SL_MULT_MIN

        if self.model is None:
            raise RuntimeError("모델이 로드되지 않음. load() 또는 train() 먼저 실행")

        obs = observation.reshape(1, -1).astype(np.float32)
        action, _ = self.model.predict(obs, deterministic=True)
        raw = float(np.clip(action[0], -1.0, 1.0))
        return SL_MULT_MIN + (raw + 1.0) * 0.5 * (SL_MULT_MAX - SL_MULT_MIN)

    def predict_batch(self, observations: np.ndarray) -> np.ndarray:
        """
        배치 관측에 대한 SL 배수 예측.

        Parameters
        ----------
        observations : np.ndarray
            shape (N, 13)

        Returns
        -------
        sl_mults : np.ndarray
            shape (N,) — SL 배수 [0.5, 3.0]
        """
        from rl.env.sl_env import SL_MULT_MAX, SL_MULT_MIN

        mults = []
        for obs in observations:
            mults.append(self.predict(obs))
        return np.array(mults, dtype=np.float32)

    def train(
        self,
        signals_csv: str | Path,
        ohlcv_dir: str | Path,
        config: TrainConfig | None = None,
        max_signals: int | None = None,
    ) -> dict[str, list[EvalResult]]:
        """
        Walk-Forward 3윈도우 학습.

        각 윈도우에서:
        1. Train 구간으로 PPO 학습
        2. Val 구간으로 얼리스탑 / 하이퍼파라미터 선택
        3. Test 구간으로 최종 성능 측정

        Returns
        -------
        results : dict
            윈도우별 EvalResult 리스트
        """
        from stable_baselines3 import PPO
        from stable_baselines3.common.callbacks import EvalCallback

        from rl.env.sl_env import SLAdjustEnv
        from rl.features.state_builder import build_from_csv

        cfg = config or self.config
        output_dir = Path(cfg.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 전체 데이터 로드
        logger.info(f"시그널 로드 중: {signals_csv}")
        signals_df = pd.read_csv(signals_csv)
        signals_df["entry_time"] = pd.to_datetime(signals_df["entry_time"])

        all_results: dict[str, list[EvalResult]] = {}

        for window in WALK_FORWARD_WINDOWS:
            wname = window["name"]
            logger.info(f"\n{'='*60}")
            logger.info(f"Walk-Forward {wname} 시작")
            logger.info(f"  Train: ~{window['train_end']}")
            logger.info(f"  Val:   ~{window['val_end']}")
            logger.info(f"  Test:  ~{window['test_end']}")
            logger.info(f"{'='*60}")

            # 시간 기반 분할 (candle_idx 버그 교훈: 시간 기반 분리 강화)
            train_mask = signals_df["entry_time"] <= window["train_end"]
            val_mask = (signals_df["entry_time"] > window["train_end"]) & (
                signals_df["entry_time"] <= window["val_end"]
            )
            test_mask = (signals_df["entry_time"] > window["val_end"]) & (
                signals_df["entry_time"] <= window["test_end"]
            )

            train_df = signals_df[train_mask]
            val_df = signals_df[val_mask]
            test_df = signals_df[test_mask]

            if len(train_df) == 0 or len(val_df) == 0:
                logger.warning(f"{wname}: Train 또는 Val 데이터 부족, 스킵")
                continue

            logger.info(
                f"  데이터: Train {len(train_df)}, Val {len(val_df)}, Test {len(test_df)}"
            )

            # 관측 벡터 빌드
            from rl.features.state_builder import build_observations

            train_data = build_observations(train_df, ohlcv_dir, max_signals=max_signals)
            val_data = build_observations(val_df, ohlcv_dir, max_signals=max_signals)

            if len(train_data["observations"]) == 0:
                logger.warning(f"{wname}: Train 관측 벡터 0건, 스킵")
                continue

            # 학습 환경
            train_env = SLAdjustEnv(
                signals=train_data["observations"],
                ohlc_slices=train_data["ohlc_slices"],
                base_sl_distances=train_data["base_sl_distances"],
                tp_distances=train_data["tp_distances"],
                entry_prices=train_data["entry_prices"],
                directions=train_data["directions"],
                shuffle=True,
            )

            # 검증 환경
            val_env = SLAdjustEnv(
                signals=val_data["observations"],
                ohlc_slices=val_data["ohlc_slices"],
                base_sl_distances=val_data["base_sl_distances"],
                tp_distances=val_data["tp_distances"],
                entry_prices=val_data["entry_prices"],
                directions=val_data["directions"],
                shuffle=False,
            )

            # 윈도우별 출력 디렉토리
            w_dir = output_dir / wname
            w_dir.mkdir(parents=True, exist_ok=True)

            # PPO 모델 생성
            model = PPO(
                "MlpPolicy",
                train_env,
                learning_rate=cfg.learning_rate,
                n_steps=min(cfg.n_steps, len(train_data["observations"])),
                batch_size=min(cfg.batch_size, len(train_data["observations"])),
                n_epochs=cfg.n_epochs,
                gamma=cfg.gamma,
                gae_lambda=cfg.gae_lambda,
                clip_range=cfg.clip_range,
                ent_coef=cfg.ent_coef,
                vf_coef=cfg.vf_coef,
                max_grad_norm=cfg.max_grad_norm,
                policy_kwargs={"net_arch": cfg.net_arch},
                verbose=1,
                tensorboard_log=None,  # 한국어/공백 경로 호환 위해 비활성화
            )

            # EvalCallback: Val 기준 최고 모델 저장
            eval_callback = EvalCallback(
                val_env,
                best_model_save_path=str(w_dir / "best"),
                log_path=str(w_dir / "eval_logs"),
                eval_freq=max(cfg.n_steps, 1024),
                n_eval_episodes=min(len(val_data["observations"]), 1000),
                deterministic=True,
            )

            # 학습
            logger.info(f"  PPO 학습 시작 (총 {cfg.total_timesteps} 스텝)")
            model.learn(
                total_timesteps=cfg.total_timesteps,
                callback=eval_callback,
                log_interval=cfg.log_interval,
            )

            # 최고 모델 로드
            best_path = w_dir / "best" / "best_model.zip"
            if best_path.exists():
                best_model = PPO.load(str(best_path))
                logger.info(f"  최고 모델 로드: {best_path}")
            else:
                best_model = model
                model.save(str(w_dir / "final_model"))
                logger.info(f"  최종 모델 저장 (EvalCallback 미작동)")

            self.model = best_model

            # 평가
            window_results = []

            # Val 평가
            val_result = self._evaluate(val_data, f"{wname}", "val")
            window_results.append(val_result)
            logger.info(f"  Val 결과: ROE {val_result.mean_roe:.4f} "
                        f"(기준 {val_result.mean_roe_baseline:.4f}, "
                        f"개선 {val_result.roe_improvement:+.4f})")

            # Test 평가
            if len(test_df) > 0:
                test_data = build_observations(test_df, ohlcv_dir, max_signals=max_signals)
                if len(test_data["observations"]) > 0:
                    test_result = self._evaluate(test_data, f"{wname}", "test")
                    window_results.append(test_result)
                    logger.info(f"  Test 결과: ROE {test_result.mean_roe:.4f} "
                                f"(기준 {test_result.mean_roe_baseline:.4f}, "
                                f"개선 {test_result.roe_improvement:+.4f})")

            all_results[wname] = window_results

            # 결과 JSON 저장
            self._save_results(window_results, w_dir / "results.json")

        # 전체 요약 저장
        self._save_summary(all_results, output_dir / "walk_forward_summary.json")

        return all_results

    def _evaluate(
        self,
        data: dict[str, np.ndarray | list],
        window_name: str,
        split: str,
    ) -> EvalResult:
        """
        데이터셋에 대해 동적 SL vs 고정 SL 비교 평가.
        """
        from rl.env.sl_env import LEVERAGE, FEE_RATE, TIMEOUT_CANDLES

        observations = data["observations"]
        ohlc_slices = data["ohlc_slices"]
        base_sl_dists = data["base_sl_distances"]
        tp_dists = data["tp_distances"]
        entries = data["entry_prices"]
        dirs = data["directions"]

        n = len(observations)
        roe_dynamic = []
        roe_baseline = []
        exit_types = []
        sl_mults = []

        for i in range(n):
            entry = entries[i]
            direction = dirs[i]
            base_sl = base_sl_dists[i]
            tp_dist = tp_dists[i]
            ohlc = ohlc_slices[i]

            # 동적 SL
            mult = self.predict(observations[i])
            sl_mults.append(mult)

            dyn_roe, dyn_exit = self._sim_single(
                ohlc, entry, base_sl * mult, tp_dist, direction
            )
            roe_dynamic.append(dyn_roe)
            exit_types.append(dyn_exit)

            # 고정 SL (배수 1.0)
            base_roe, _ = self._sim_single(
                ohlc, entry, base_sl, tp_dist, direction
            )
            roe_baseline.append(base_roe)

        roe_dynamic = np.array(roe_dynamic)
        roe_baseline = np.array(roe_baseline)
        exit_types = np.array(exit_types)

        tp_rate = np.mean(exit_types == "tp")
        sl_rate = np.mean(exit_types == "sl")
        timeout_rate = np.mean(exit_types == "timeout")

        # Profit Factor 계산
        pf_dynamic = self._calc_pf(roe_dynamic)
        pf_baseline = self._calc_pf(roe_baseline)

        return EvalResult(
            window_name=window_name,
            split=split,
            n_signals=n,
            mean_roe=float(np.mean(roe_dynamic)),
            mean_roe_baseline=float(np.mean(roe_baseline)),
            roe_improvement=float(np.mean(roe_dynamic) - np.mean(roe_baseline)),
            tp_rate=float(tp_rate),
            sl_rate=float(sl_rate),
            timeout_rate=float(timeout_rate),
            mean_sl_mult=float(np.mean(sl_mults)),
            profit_factor=pf_dynamic,
            profit_factor_baseline=pf_baseline,
        )

    def _sim_single(
        self,
        ohlc: np.ndarray,
        entry: float,
        sl_dist: float,
        tp_dist: float,
        direction: float,
    ) -> tuple[float, str]:
        """단일 시그널 SL/TP 시뮬레이션 → (roe, exit_type)."""
        from rl.env.sl_env import FEE_RATE, LEVERAGE

        if direction > 0:
            sl_price = entry - sl_dist
            tp_price = entry + tp_dist
        else:
            sl_price = entry + sl_dist
            tp_price = entry - tp_dist

        exit_type = "timeout"
        exit_price = ohlc[-1, 3] if len(ohlc) > 0 else entry

        for c in range(len(ohlc)):
            h, l = ohlc[c, 1], ohlc[c, 2]
            if direction > 0:
                if l <= sl_price:
                    exit_type, exit_price = "sl", sl_price
                    break
                if h >= tp_price:
                    exit_type, exit_price = "tp", tp_price
                    break
            else:
                if h >= sl_price:
                    exit_type, exit_price = "sl", sl_price
                    break
                if l <= tp_price:
                    exit_type, exit_price = "tp", tp_price
                    break

        if direction > 0:
            pnl_pct = (exit_price - entry) / entry
        else:
            pnl_pct = (entry - exit_price) / entry

        roe = pnl_pct * LEVERAGE - 2 * FEE_RATE * LEVERAGE
        return float(roe), exit_type

    @staticmethod
    def _calc_pf(roe_array: np.ndarray) -> float:
        """Profit Factor 계산."""
        gross_profit = float(np.sum(roe_array[roe_array > 0]))
        gross_loss = float(np.abs(np.sum(roe_array[roe_array < 0])))
        if gross_loss < 1e-8:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    @staticmethod
    def _save_results(results: list[EvalResult], path: Path):
        """평가 결과 JSON 저장."""
        data = []
        for r in results:
            data.append({
                "window": r.window_name,
                "split": r.split,
                "n_signals": r.n_signals,
                "mean_roe": round(r.mean_roe, 6),
                "mean_roe_baseline": round(r.mean_roe_baseline, 6),
                "roe_improvement": round(r.roe_improvement, 6),
                "tp_rate": round(r.tp_rate, 4),
                "sl_rate": round(r.sl_rate, 4),
                "timeout_rate": round(r.timeout_rate, 4),
                "mean_sl_mult": round(r.mean_sl_mult, 4),
                "profit_factor": round(r.profit_factor, 4),
                "profit_factor_baseline": round(r.profit_factor_baseline, 4),
            })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"결과 저장: {path}")

    @staticmethod
    def _save_summary(all_results: dict[str, list[EvalResult]], path: Path):
        """Walk-Forward 전체 요약 저장."""
        summary = {"windows": {}}
        for wname, results in all_results.items():
            summary["windows"][wname] = {}
            for r in results:
                summary["windows"][wname][r.split] = {
                    "n_signals": r.n_signals,
                    "mean_roe": round(r.mean_roe, 6),
                    "roe_improvement": round(r.roe_improvement, 6),
                    "profit_factor": round(r.profit_factor, 4),
                    "profit_factor_baseline": round(r.profit_factor_baseline, 4),
                }

        # 전체 윈도우 Test 평균
        test_improvements = []
        test_pfs = []
        for results in all_results.values():
            for r in results:
                if r.split == "test":
                    test_improvements.append(r.roe_improvement)
                    test_pfs.append(r.profit_factor)

        if test_improvements:
            summary["aggregate_test"] = {
                "mean_roe_improvement": round(float(np.mean(test_improvements)), 6),
                "mean_profit_factor": round(float(np.mean(test_pfs)), 4),
                "n_windows": len(test_improvements),
            }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        logger.info(f"Walk-Forward 요약 저장: {path}")


# ── CLI 인터페이스 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SL Agent — 동적 스탑로스 RL 에이전트 (Phase 4)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # train 커맨드
    train_parser = subparsers.add_parser("train", help="Walk-Forward 학습")
    train_parser.add_argument(
        "--signals", required=True, help="라벨링된 시그널 CSV 경로"
    )
    train_parser.add_argument(
        "--ohlcv", required=True, help="코인별 15분봉 OHLCV 디렉토리"
    )
    train_parser.add_argument(
        "--output", default="experiments/sl_agent", help="출력 디렉토리"
    )
    train_parser.add_argument(
        "--total-steps", type=int, default=500_000, help="학습 총 스텝"
    )
    train_parser.add_argument(
        "--max-signals", type=int, default=None, help="디버깅용 최대 시그널 수"
    )
    train_parser.add_argument("--lr", type=float, default=3e-4, help="학습률")
    train_parser.add_argument("--batch-size", type=int, default=256, help="배치 크기")

    # eval 커맨드
    eval_parser = subparsers.add_parser("eval", help="저장된 모델 평가")
    eval_parser.add_argument("--model", required=True, help="모델 경로 (.zip)")
    eval_parser.add_argument(
        "--signals", required=True, help="평가할 시그널 CSV 경로"
    )
    eval_parser.add_argument(
        "--ohlcv", required=True, help="코인별 15분봉 OHLCV 디렉토리"
    )
    eval_parser.add_argument(
        "--max-signals", type=int, default=None, help="최대 시그널 수"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.command == "train":
        config = TrainConfig(
            learning_rate=args.lr,
            batch_size=args.batch_size,
            total_timesteps=args.total_steps,
            output_dir=args.output,
        )
        agent = SLAgent(config=config)
        results = agent.train(
            signals_csv=args.signals,
            ohlcv_dir=args.ohlcv,
            config=config,
            max_signals=args.max_signals,
        )

        # 결과 요약 출력
        print("\n" + "=" * 60)
        print("Walk-Forward 학습 완료")
        print("=" * 60)
        for wname, w_results in results.items():
            for r in w_results:
                print(
                    f"  {r.window_name}/{r.split}: "
                    f"ROE {r.mean_roe:+.4f} (기준 {r.mean_roe_baseline:+.4f}, "
                    f"개선 {r.roe_improvement:+.4f}) "
                    f"PF {r.profit_factor:.2f} (기준 {r.profit_factor_baseline:.2f}) "
                    f"TP {r.tp_rate:.1%} SL {r.sl_rate:.1%} "
                    f"평균배수 {r.mean_sl_mult:.2f}"
                )

    elif args.command == "eval":
        agent = SLAgent.load(args.model)

        from rl.features.state_builder import build_from_csv

        data = build_from_csv(args.signals, args.ohlcv, max_signals=args.max_signals)

        result = agent._evaluate(data, "manual_eval", "eval")

        print("\n" + "=" * 60)
        print("평가 결과")
        print("=" * 60)
        print(f"  시그널 수:     {result.n_signals}")
        print(f"  평균 ROE:      {result.mean_roe:+.4f}")
        print(f"  기준 ROE:      {result.mean_roe_baseline:+.4f}")
        print(f"  ROE 개선:      {result.roe_improvement:+.4f}")
        print(f"  Profit Factor: {result.profit_factor:.4f} (기준 {result.profit_factor_baseline:.4f})")
        print(f"  TP 비율:       {result.tp_rate:.1%}")
        print(f"  SL 비율:       {result.sl_rate:.1%}")
        print(f"  Timeout 비율:  {result.timeout_rate:.1%}")
        print(f"  평균 SL 배수:  {result.mean_sl_mult:.2f}")


if __name__ == "__main__":
    main()
