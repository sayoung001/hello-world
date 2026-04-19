"""
walk_forward.py — Walk-Forward 3윈도우 백테스트 프레임워크
===========================================================
CLAUDE.md 규칙 준수:
  Window 1: Train ~2025-03 → Val ~2025-06 → Test ~2025-09
  Window 2: Train ~2025-06 → Val ~2025-09 → Test ~2025-12
  Window 3: Train ~2025-09 → Val ~2025-12 → Test ~2026-03

평가 항목:
  - Baseline PF (기존 고정 SL) vs Enhanced PF (리스크 파이프라인 적용)
  - 리스크 필터로 거부된 시그널의 원래 결과 분석
  - SL 히트 감소율, ROE 개선량

데이터:
  - signals_all_labeled.csv (370만 시그널, outcome_labeler.py 출력)
  - 컬럼: exit_type, exit_time, exit_price, realized_roe, hold_candles,
           mae_pct, mfe_pct, direction_correct, ...

사용법:
  # Windows (데이터가 있는 곳)
  python backtests/walk_forward.py --data D:/AutoTrade/Raw_Data/labeled_signals/signals_all_labeled.csv

  # 또는 환경변수
  set LABELED_SIGNALS_PATH=D:/AutoTrade/Raw_Data/labeled_signals/signals_all_labeled.csv
  python backtests/walk_forward.py
"""

from __future__ import annotations
import os
import sys
import time
import argparse
from datetime import datetime
from collections import defaultdict
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pandas as pd
except ImportError:
    pd = None

from risk.pipeline import RiskPipeline
from risk.models import PipelineContext, PositionContext, MarketState
from agents.memory.rule_store import RuleStore
from agents.memory.trading_brain import TradingBrain
from agents.memory.reflection_engine import ReflectionEngine
from agents.enriched_state import EnrichedStateBuilder

# ─────────────────────────────────────
# Walk-Forward 윈도우 정의
# ─────────────────────────────────────
WINDOWS = [
    {
        "name": "Window 1",
        "train": ("2023-01-01", "2025-03-31"),
        "val":   ("2025-04-01", "2025-06-30"),
        "test":  ("2025-07-01", "2025-09-30"),
    },
    {
        "name": "Window 2",
        "train": ("2023-01-01", "2025-06-30"),
        "val":   ("2025-07-01", "2025-09-30"),
        "test":  ("2025-10-01", "2025-12-31"),
    },
    {
        "name": "Window 3",
        "train": ("2023-01-01", "2025-09-30"),
        "val":   ("2025-10-01", "2025-12-31"),
        "test":  ("2026-01-01", "2026-03-31"),
    },
]

# 시그널 레벨 필터 (CLAUDE.md 기준)
SIGNAL_LEVELS = {
    "production": {"min_conf": 60, "min_adx": 30, "max_gap": 0.5},
    "level1":     {"min_conf": 50, "min_adx": 25, "max_gap": 0.7},
    "level2":     {"min_conf": 40, "min_adx": 20, "max_gap": 0.8},
    "level3":     {"min_conf": 30, "min_adx": 15, "max_gap": 1.0},
}

LEVERAGE = 20


class WalkForwardBacktest:
    """
    Walk-Forward 3윈도우 백테스트.

    비교:
    A) Baseline: 기존 고정 SL (진입 조건만 필터)
    B) Enhanced: Baseline + 리스크 파이프라인 필터 + 동적 레버리지
    C) Enhanced+Memory: B + TradingBrain 자문 반영 (학습 루프)
    """

    def __init__(self, data_path: str, level: str = "production"):
        if pd is None:
            raise ImportError("pandas 필요: pip install pandas")
        self.data_path = data_path
        self.level = level
        self.level_config = SIGNAL_LEVELS[level]
        self.results: list[dict] = []

    def load_data(self) -> pd.DataFrame:
        """labeled signals CSV 로드 + 전처리"""
        print(f"  데이터 로드: {self.data_path}")
        df = pd.read_csv(self.data_path, parse_dates=["timestamp"])
        df = df.sort_values("timestamp").reset_index(drop=True)
        print(f"  총 {len(df):,}행 로드, "
              f"{df['timestamp'].min()} ~ {df['timestamp'].max()}")
        return df

    def filter_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """시그널 레벨 필터 적용"""
        cfg = self.level_config
        mask = (
            (df["confidence"] >= cfg["min_conf"]) &
            (df["adx"] >= cfg["min_adx"]) &
            (df["gap"].abs() <= cfg["max_gap"])
        )
        return df[mask].copy()

    def run_all_windows(self, df: pd.DataFrame) -> list[dict]:
        """3윈도우 순차 실행"""
        for window in WINDOWS:
            print(f"\n{'='*60}")
            print(f"  {window['name']}")
            print(f"{'='*60}")
            result = self.run_single_window(df, window)
            self.results.append(result)
        return self.results

    def run_single_window(self, df: pd.DataFrame, window: dict) -> dict:
        """단일 윈도우 실행"""
        test_start, test_end = window["test"]
        train_start, train_end = window["train"]
        val_start, val_end = window["val"]

        # 시간 필터
        train_df = df[(df["timestamp"] >= train_start) &
                      (df["timestamp"] <= train_end)]
        val_df = df[(df["timestamp"] >= val_start) &
                    (df["timestamp"] <= val_end)]
        test_df = df[(df["timestamp"] >= test_start) &
                     (df["timestamp"] <= test_end)]

        # 레벨 필터
        train_signals = self.filter_signals(train_df)
        val_signals = self.filter_signals(val_df)
        test_signals = self.filter_signals(test_df)

        print(f"  Train: {len(train_signals):,}건 "
              f"({train_start} ~ {train_end})")
        print(f"  Val:   {len(val_signals):,}건 "
              f"({val_start} ~ {val_end})")
        print(f"  Test:  {len(test_signals):,}건 "
              f"({test_start} ~ {test_end})")

        # A) Baseline (기존 방식)
        baseline = self._evaluate_baseline(test_signals)

        # B) Enhanced (리스크 파이프라인 필터)
        enhanced = self._evaluate_enhanced(test_signals)

        # C) Enhanced + Memory (학습 루프 — train에서 학습 후 test)
        memory_enhanced = self._evaluate_with_memory(
            train_signals, val_signals, test_signals
        )

        result = {
            "window": window["name"],
            "test_period": f"{test_start} ~ {test_end}",
            "test_signals": len(test_signals),
            "baseline": baseline,
            "enhanced": enhanced,
            "memory_enhanced": memory_enhanced,
        }

        self._print_comparison(result)
        return result

    def _evaluate_baseline(self, signals: pd.DataFrame) -> dict:
        """A) Baseline — 기존 고정 SL"""
        if signals.empty:
            return self._empty_result("baseline")

        tp = signals[signals["exit_type"] == "TP"]
        sl = signals[signals["exit_type"] == "SL"]
        timeout = signals[signals["exit_type"] == "Timeout"]

        total_roe = signals["realized_roe"].sum()
        win_roe = tp["realized_roe"].sum() if len(tp) > 0 else 0
        loss_roe = abs(sl["realized_roe"].sum()) if len(sl) > 0 else 1

        return {
            "method": "baseline",
            "total": len(signals),
            "tp": len(tp),
            "sl": len(sl),
            "timeout": len(timeout),
            "winrate": len(tp) / len(signals) if len(signals) > 0 else 0,
            "pf": win_roe / loss_roe if loss_roe > 0 else float("inf"),
            "total_roe": total_roe,
            "avg_roe": signals["realized_roe"].mean(),
            "sl_hit_rate": len(sl) / len(signals) if len(signals) > 0 else 0,
            "sl_direction_correct": (
                sl["direction_correct"].mean() if len(sl) > 0 else 0
            ),
        }

    def _evaluate_enhanced(self, signals: pd.DataFrame) -> dict:
        """B) Enhanced — 리스크 파이프라인 필터"""
        if signals.empty:
            return self._empty_result("enhanced")

        pipeline = RiskPipeline()
        accepted = []
        rejected = []
        leverage_adjustments = []

        for _, row in signals.iterrows():
            signal = row.to_dict()
            market_data = self._extract_market_data(row)

            ctx = PipelineContext(
                signal=signal,
                market_state=MarketState(**market_data),
            )

            try:
                decision = pipeline.evaluate(ctx)
                risk_level = decision.risk_level.value
                leverage = decision.leverage_recommendation.recommended_leverage

                if risk_level in ("low", "medium"):
                    accepted.append({
                        **signal,
                        "adjusted_leverage": leverage,
                        "risk_level": risk_level,
                    })
                    leverage_adjustments.append(leverage)
                else:
                    rejected.append({
                        **signal,
                        "risk_level": risk_level,
                        "original_exit": row.get("exit_type", ""),
                        "original_roe": row.get("realized_roe", 0),
                    })
            except Exception:
                accepted.append({**signal, "adjusted_leverage": LEVERAGE,
                                 "risk_level": "unknown"})
                leverage_adjustments.append(LEVERAGE)

        accepted_df = pd.DataFrame(accepted) if accepted else pd.DataFrame()
        rejected_df = pd.DataFrame(rejected) if rejected else pd.DataFrame()

        if accepted_df.empty:
            return self._empty_result("enhanced")

        tp = accepted_df[accepted_df["exit_type"] == "TP"]
        sl = accepted_df[accepted_df["exit_type"] == "SL"]

        # 레버리지 조정 반영 ROE
        adjusted_roe = 0
        for _, r in accepted_df.iterrows():
            lev = r.get("adjusted_leverage", LEVERAGE)
            base_pnl = r.get("realized_roe", 0) / LEVERAGE  # PnL%
            adjusted_roe += base_pnl * lev

        win_roe = tp["realized_roe"].sum() if len(tp) > 0 else 0
        loss_roe = abs(sl["realized_roe"].sum()) if len(sl) > 0 else 1

        # 거부된 시그널 분석
        rejected_would_sl = 0
        rejected_would_tp = 0
        if not rejected_df.empty:
            rejected_would_sl = len(
                rejected_df[rejected_df["original_exit"] == "SL"]
            )
            rejected_would_tp = len(
                rejected_df[rejected_df["original_exit"] == "TP"]
            )

        return {
            "method": "enhanced",
            "total": len(signals),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "rejection_rate": len(rejected) / len(signals) if len(signals) > 0 else 0,
            "tp": len(tp),
            "sl": len(sl),
            "winrate": len(tp) / len(accepted) if len(accepted) > 0 else 0,
            "pf": win_roe / loss_roe if loss_roe > 0 else float("inf"),
            "total_roe": adjusted_roe,
            "avg_roe": adjusted_roe / len(accepted) if len(accepted) > 0 else 0,
            "avg_leverage": sum(leverage_adjustments) / len(leverage_adjustments)
                           if leverage_adjustments else LEVERAGE,
            "sl_hit_rate": len(sl) / len(accepted) if len(accepted) > 0 else 0,
            "rejected_would_sl": rejected_would_sl,
            "rejected_would_tp": rejected_would_tp,
            "rejection_accuracy": (
                rejected_would_sl / len(rejected) if len(rejected) > 0 else 0
            ),
        }

    def _evaluate_with_memory(self, train: pd.DataFrame,
                               val: pd.DataFrame,
                               test: pd.DataFrame) -> dict:
        """C) Enhanced + Memory — 학습 루프"""
        if test.empty:
            return self._empty_result("memory_enhanced")

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp_path = f.name

        try:
            store = RuleStore(rules_path=tmp_path)
            brain = TradingBrain(rule_store=store)
            reflection = ReflectionEngine(rule_store=store)
            builder = EnrichedStateBuilder(brain=brain)
            pipeline = RiskPipeline()

            # 학습 단계: train 시그널로 메모리 학습
            for _, row in train.iterrows():
                trade_record = {
                    "symbol": row.get("symbol", ""),
                    "direction": row.get("direction", ""),
                    "exit_type": row.get("exit_type", ""),
                    "roe": row.get("realized_roe", 0),
                    "gap": float(row.get("gap", 0)),
                    "confidence": row.get("confidence", 0),
                    "adx": float(row.get("adx", 0)),
                    "hold_candles": int(row.get("hold_candles", 0)),
                    "direction_correct": bool(row.get("direction_correct", False)),
                    "market_state": self._extract_market_data(row),
                }
                reflection.reflect(trade_record)
                brain.record_trade_result(
                    row.get("symbol", ""),
                    row.get("direction", ""),
                    float(row.get("gap", 0)),
                    row.get("exit_type", ""),
                    row.get("realized_roe", 0),
                )

            print(f"  학습 완료: {store.count}개 규칙, "
                  f"{brain.get_stats()['coins_tracked']}개 코인")

            # 테스트 단계: 리스크 파이프라인 + 메모리 자문
            accepted = []
            rejected = []
            brain_filtered = 0

            for _, row in test.iterrows():
                signal = row.to_dict()
                market_data = self._extract_market_data(row)

                enriched = builder.build(
                    signal=signal,
                    market_data=market_data,
                )
                brain_advice = enriched.get("brain_advice", {})
                brain_conf = brain_advice.get("brain_confidence", 0.5)

                # Brain 신뢰도가 매우 낮으면 거부
                if brain_conf < 0.25:
                    brain_filtered += 1
                    rejected.append({
                        **signal,
                        "risk_level": "brain_low",
                    })
                    continue

                ctx = enriched["pipeline_context"]
                try:
                    decision = pipeline.evaluate(ctx)
                    risk_level = decision.risk_level.value
                    leverage = decision.leverage_recommendation.recommended_leverage

                    if risk_level in ("low", "medium"):
                        accepted.append({
                            **signal,
                            "adjusted_leverage": leverage,
                            "risk_level": risk_level,
                            "brain_confidence": brain_conf,
                        })
                    else:
                        rejected.append({
                            **signal,
                            "risk_level": risk_level,
                        })
                except Exception:
                    accepted.append({
                        **signal,
                        "adjusted_leverage": LEVERAGE,
                        "risk_level": "unknown",
                    })

                # 테스트 중에도 학습 (온라인 학습)
                brain.record_trade_result(
                    row.get("symbol", ""),
                    row.get("direction", ""),
                    float(row.get("gap", 0)),
                    row.get("exit_type", ""),
                    row.get("realized_roe", 0),
                )

            accepted_df = pd.DataFrame(accepted) if accepted else pd.DataFrame()
            rejected_df = pd.DataFrame(rejected) if rejected else pd.DataFrame()

            if accepted_df.empty:
                return self._empty_result("memory_enhanced")

            tp = accepted_df[accepted_df["exit_type"] == "TP"]
            sl = accepted_df[accepted_df["exit_type"] == "SL"]

            adjusted_roe = 0
            for _, r in accepted_df.iterrows():
                lev = r.get("adjusted_leverage", LEVERAGE)
                base_pnl = r.get("realized_roe", 0) / LEVERAGE
                adjusted_roe += base_pnl * lev

            win_roe = tp["realized_roe"].sum() if len(tp) > 0 else 0
            loss_roe = abs(sl["realized_roe"].sum()) if len(sl) > 0 else 1

            return {
                "method": "memory_enhanced",
                "total": len(test),
                "accepted": len(accepted),
                "rejected": len(rejected),
                "brain_filtered": brain_filtered,
                "rejection_rate": len(rejected) / len(test) if len(test) > 0 else 0,
                "tp": len(tp),
                "sl": len(sl),
                "winrate": len(tp) / len(accepted) if len(accepted) > 0 else 0,
                "pf": win_roe / loss_roe if loss_roe > 0 else float("inf"),
                "total_roe": adjusted_roe,
                "avg_roe": adjusted_roe / len(accepted) if len(accepted) > 0 else 0,
                "sl_hit_rate": len(sl) / len(accepted) if len(accepted) > 0 else 0,
                "rules_learned": store.count,
                "coins_tracked": brain.get_stats()["coins_tracked"],
            }
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    def _extract_market_data(self, row) -> dict:
        """시그널 행에서 MarketState 필드 추출"""
        return {
            "btc_price": float(row.get("btc_price", 0)),
            "btc_level": int(row.get("btc_level", 0)),
            "btc_atr_pct": float(row.get("atr_pct", row.get("btc_atr_pct", 0))),
            "fear_greed": int(row.get("fear_greed", row.get("fgi", 50))),
            "funding_rate": float(row.get("funding_rate", 0)),
            "btc_rsi": float(row.get("btc_rsi", 50)),
            "btc_change_24h": float(row.get("btc_change_24h", 0)),
        }

    def _empty_result(self, method: str) -> dict:
        return {"method": method, "total": 0, "note": "데이터 없음"}

    def _print_comparison(self, result: dict):
        """윈도우 결과 비교 출력"""
        print(f"\n  ── {result['window']} 결과 ({result['test_period']}) ──")
        print(f"  테스트 시그널: {result['test_signals']:,}건")

        for key in ("baseline", "enhanced", "memory_enhanced"):
            r = result[key]
            if r.get("total", 0) == 0:
                print(f"  [{r['method']}] 데이터 없음")
                continue

            label = {"baseline": "A) Baseline", "enhanced": "B) Enhanced",
                     "memory_enhanced": "C) Memory"}[key]
            print(f"\n  {label}:")
            print(f"    거래 수: {r.get('accepted', r.get('total', 0)):,}")
            print(f"    승률: {r.get('winrate', 0):.1%}")
            print(f"    PF: {r.get('pf', 0):.3f}")
            print(f"    총 ROE: {r.get('total_roe', 0):+,.1f}%")
            print(f"    SL 히트율: {r.get('sl_hit_rate', 0):.1%}")

            if key != "baseline":
                print(f"    거부율: {r.get('rejection_rate', 0):.1%}")
                if key == "memory_enhanced":
                    print(f"    Brain 거부: {r.get('brain_filtered', 0)}건")
                    print(f"    학습 규칙: {r.get('rules_learned', 0)}개")

    def generate_report(self) -> str:
        """전체 결과 마크다운 리포트 생성"""
        lines = [
            "# Walk-Forward 백테스트 결과",
            f"\n> 시그널 레벨: {self.level}",
            f"> 데이터: {self.data_path}",
            f"> 실행 시점: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        for result in self.results:
            lines.append(f"\n## {result['window']} ({result['test_period']})")
            lines.append(f"\n테스트 시그널: {result['test_signals']:,}건\n")
            lines.append("| 지표 | A) Baseline | B) Enhanced | C) Memory |")
            lines.append("|------|-----------|-----------|---------|")

            b = result.get("baseline", {})
            e = result.get("enhanced", {})
            m = result.get("memory_enhanced", {})

            rows = [
                ("거래 수", b.get("total", 0), e.get("accepted", 0), m.get("accepted", 0)),
                ("승률", f"{b.get('winrate', 0):.1%}", f"{e.get('winrate', 0):.1%}", f"{m.get('winrate', 0):.1%}"),
                ("PF", f"{b.get('pf', 0):.3f}", f"{e.get('pf', 0):.3f}", f"{m.get('pf', 0):.3f}"),
                ("총 ROE", f"{b.get('total_roe', 0):+,.1f}%", f"{e.get('total_roe', 0):+,.1f}%", f"{m.get('total_roe', 0):+,.1f}%"),
                ("SL 히트율", f"{b.get('sl_hit_rate', 0):.1%}", f"{e.get('sl_hit_rate', 0):.1%}", f"{m.get('sl_hit_rate', 0):.1%}"),
                ("거부율", "-", f"{e.get('rejection_rate', 0):.1%}", f"{m.get('rejection_rate', 0):.1%}"),
            ]
            for label, *vals in rows:
                lines.append(f"| {label} | {' | '.join(str(v) for v in vals)} |")

        # PF 변화 요약
        lines.append("\n## PF 변화 요약\n")
        lines.append("| 윈도우 | Baseline PF | Enhanced PF | Memory PF | 개선 |")
        lines.append("|--------|-----------|-----------|---------|------|")
        for result in self.results:
            b_pf = result.get("baseline", {}).get("pf", 0)
            e_pf = result.get("enhanced", {}).get("pf", 0)
            m_pf = result.get("memory_enhanced", {}).get("pf", 0)
            best_pf = max(e_pf, m_pf)
            improvement = (best_pf / b_pf - 1) * 100 if b_pf > 0 else 0
            lines.append(f"| {result['window']} | {b_pf:.3f} | {e_pf:.3f} | "
                         f"{m_pf:.3f} | {improvement:+.1f}% |")

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Walk-Forward 백테스트")
    parser.add_argument(
        "--data", type=str,
        default=os.environ.get(
            "LABELED_SIGNALS_PATH",
            "D:/AutoTrade/Raw_Data/labeled_signals/signals_all_labeled.csv"
        ),
        help="labeled signals CSV 경로",
    )
    parser.add_argument(
        "--level", type=str, default="production",
        choices=list(SIGNAL_LEVELS.keys()),
        help="시그널 레벨 (production/level1/level2/level3)",
    )
    parser.add_argument(
        "--output", type=str, default="",
        help="결과 마크다운 저장 경로 (기본: stdout)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.data):
        print(f"  ❌ 데이터 파일 없음: {args.data}")
        print(f"  환경변수 LABELED_SIGNALS_PATH를 설정하거나 --data 옵션 사용")
        sys.exit(1)

    bt = WalkForwardBacktest(data_path=args.data, level=args.level)
    df = bt.load_data()
    bt.run_all_windows(df)

    report = bt.generate_report()
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n  ✅ 리포트 저장: {args.output}")
    else:
        print("\n" + report)


if __name__ == "__main__":
    main()
