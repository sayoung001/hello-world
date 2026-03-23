"""
Dual Momentum Strategy Backtest
===============================
크립토 시장용 듀얼 모멘텀 전략 백테스트.
상대 모멘텀 + 절대 모멘텀을 결합하여 추세 추종.

사용법:
    python backtests/dual_momentum.py

필요 패키지:
    pip install pandas numpy matplotlib
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from dataclasses import dataclass, field


@dataclass
class MomentumTrade:
    entry_time: object
    exit_time: object
    asset: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    hold_days: int


@dataclass
class MomentumBacktestResult:
    trades: list = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    total_trades: int = 0
    time_in_market_pct: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0


def generate_multi_asset_data(
    days: int = 730,
    seed: int = 42,
) -> dict[str, pd.DataFrame]:
    """다수 크립토 자산의 일봉 데이터 생성."""
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=days, freq="D")

    assets = {
        "BTC": {"price": 30000, "drift": 0.0003, "vol": 0.03},
        "ETH": {"price": 2000, "drift": 0.0004, "vol": 0.04},
        "SOL": {"price": 100, "drift": 0.0005, "vol": 0.05},
        "BNB": {"price": 300, "drift": 0.0002, "vol": 0.03},
    }

    data = {}
    for name, params in assets.items():
        prices = [params["price"]]
        for _ in range(days - 1):
            # 추세 + 랜덤 + 간헐적 급등/급락
            ret = np.random.normal(params["drift"], params["vol"])
            if np.random.random() < 0.02:  # 2% 확률 급변
                ret += np.random.choice([-1, 1]) * np.random.uniform(0.05, 0.15)
            prices.append(prices[-1] * (1 + ret))

        prices = np.array(prices)
        high = prices * (1 + np.abs(np.random.normal(0, 0.01, days)))
        low = prices * (1 - np.abs(np.random.normal(0, 0.01, days)))

        data[name] = pd.DataFrame(
            {
                "open": prices,
                "high": high,
                "low": low,
                "close": prices,
                "volume": np.random.uniform(1e6, 1e8, days),
            },
            index=dates,
        )

    return data


def calculate_momentum(prices: pd.Series, lookback: int) -> pd.Series:
    """모멘텀 스코어 계산 (수익률 기반)."""
    return prices / prices.shift(lookback) - 1


def run_dual_momentum_backtest(
    data: dict[str, pd.DataFrame],
    initial_capital: float = 10000.0,
    lookback: int = 30,
    rebalance_days: int = 7,
    top_n: int = 2,
    fee_rate: float = 0.001,
) -> MomentumBacktestResult:
    """Dual Momentum 백테스트 실행."""
    # 종가 데이터 취합
    close_prices = pd.DataFrame({name: df["close"] for name, df in data.items()})
    dates = close_prices.index

    capital = initial_capital
    current_holdings = {}  # {asset: (qty, entry_price, entry_time)}
    trades = []
    equity = []
    days_in_market = 0

    for i in range(lookback, len(dates)):
        date = dates[i]

        # 리밸런싱 주기 확인
        if (i - lookback) % rebalance_days == 0:
            # 1. 상대 모멘텀: 각 자산의 룩백 수익률 계산
            momentum_scores = {}
            for asset in close_prices.columns:
                mom = (
                    close_prices[asset].iloc[i]
                    / close_prices[asset].iloc[i - lookback]
                    - 1
                )
                momentum_scores[asset] = mom

            # 상위 N개 선택
            sorted_assets = sorted(
                momentum_scores.items(), key=lambda x: x[1], reverse=True
            )
            top_assets = sorted_assets[:top_n]

            # 2. 절대 모멘텀: 수익률 > 0인 자산만 유지
            selected = [
                (asset, score) for asset, score in top_assets if score > 0
            ]

            # 기존 포지션 청산 (선택되지 않은 자산)
            selected_names = {asset for asset, _ in selected}
            for asset in list(current_holdings.keys()):
                if asset not in selected_names:
                    qty, entry_price, entry_time = current_holdings[asset]
                    exit_price = close_prices[asset].iloc[i]
                    proceeds = qty * exit_price * (1 - fee_rate)
                    capital += proceeds

                    pnl_pct = (exit_price / entry_price - 1) * 100
                    hold_days = (date - entry_time).days

                    trades.append(
                        MomentumTrade(
                            entry_time=entry_time,
                            exit_time=date,
                            asset=asset,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            pnl_pct=pnl_pct,
                            hold_days=hold_days,
                        )
                    )
                    del current_holdings[asset]

            # 새 포지션 진입
            if selected:
                allocation = capital / len(selected)
                for asset, _ in selected:
                    if asset not in current_holdings:
                        price = close_prices[asset].iloc[i]
                        qty = allocation / price * (1 - fee_rate)
                        cost = qty * price * (1 + fee_rate)
                        if capital >= cost:
                            capital -= cost
                            current_holdings[asset] = (qty, price, date)

        # 자산 가치 계산
        holdings_value = sum(
            qty * close_prices[asset].iloc[i]
            for asset, (qty, _, _) in current_holdings.items()
        )
        equity.append(capital + holdings_value)

        if current_holdings:
            days_in_market += 1

    equity_series = pd.Series(equity, index=dates[lookback:])

    # 최종 포지션 청산
    for asset, (qty, entry_price, entry_time) in current_holdings.items():
        exit_price = close_prices[asset].iloc[-1]
        pnl_pct = (exit_price / entry_price - 1) * 100
        hold_days = (dates[-1] - entry_time).days
        trades.append(
            MomentumTrade(
                entry_time=entry_time,
                exit_time=dates[-1],
                asset=asset,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                hold_days=hold_days,
            )
        )

    # 결과
    result = MomentumBacktestResult()
    result.trades = trades
    result.equity_curve = equity_series
    result.total_trades = len(trades)
    result.total_pnl_pct = (equity_series.iloc[-1] / initial_capital - 1) * 100

    if trades:
        wins = [t for t in trades if t.pnl_pct > 0]
        result.win_rate = len(wins) / len(trades) * 100
        result.best_trade_pct = max(t.pnl_pct for t in trades)
        result.worst_trade_pct = min(t.pnl_pct for t in trades)

    total_days = len(dates) - lookback
    result.time_in_market_pct = days_in_market / total_days * 100 if total_days > 0 else 0

    # MDD
    peak = equity_series.expanding().max()
    drawdown = (equity_series - peak) / peak * 100
    result.max_drawdown = drawdown.min()

    # Sharpe Ratio
    returns = equity_series.pct_change().dropna()
    if returns.std() > 0:
        result.sharpe_ratio = returns.mean() / returns.std() * np.sqrt(365)

    return result


def plot_momentum_results(
    data: dict[str, pd.DataFrame],
    result: MomentumBacktestResult,
    save_path: str = None,
):
    """듀얼 모멘텀 결과 시각화."""
    fig, axes = plt.subplots(3, 1, figsize=(14, 10))

    # 자산별 가격 (정규화)
    for name, df in data.items():
        normalized = df["close"] / df["close"].iloc[0] * 100
        axes[0].plot(df.index, normalized, label=name)
    axes[0].set_title("Asset Prices (Normalized to 100)")
    axes[0].legend()

    # 자산 곡선
    axes[1].plot(result.equity_curve.index, result.equity_curve, color="blue")
    axes[1].axhline(y=10000, color="gray", linestyle="--", alpha=0.5)
    axes[1].set_title("Portfolio Equity Curve")

    # 드로다운
    peak = result.equity_curve.expanding().max()
    dd = (result.equity_curve - peak) / peak * 100
    axes[2].fill_between(dd.index, dd, 0, color="red", alpha=0.3)
    axes[2].set_title("Drawdown (%)")

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150)
        print(f"Chart saved to {save_path}")
    else:
        plt.show()


def main():
    print("=" * 60)
    print("Dual Momentum Strategy Backtest")
    print("Market: Crypto (BTC, ETH, SOL, BNB simulated)")
    print("=" * 60)

    # 다중 자산 데이터 생성
    data = generate_multi_asset_data(days=730)
    for name, df in data.items():
        print(f"  {name}: ${df['close'].iloc[0]:.0f} -> ${df['close'].iloc[-1]:.0f}")

    # 백테스트 실행
    result = run_dual_momentum_backtest(
        data, lookback=30, rebalance_days=7, top_n=2
    )

    # 결과 출력
    print(f"\n{'─' * 40}")
    print("Performance Summary")
    print(f"{'─' * 40}")
    print(f"Total Trades:      {result.total_trades}")
    print(f"Win Rate:          {result.win_rate:.1f}%")
    print(f"Total PnL:         {result.total_pnl_pct:.2f}%")
    print(f"Max Drawdown:      {result.max_drawdown:.2f}%")
    print(f"Sharpe Ratio:      {result.sharpe_ratio:.2f}")
    print(f"Time in Market:    {result.time_in_market_pct:.1f}%")
    print(f"Best Trade:        {result.best_trade_pct:.2f}%")
    print(f"Worst Trade:       {result.worst_trade_pct:.2f}%")

    # 자산별 거래 통계
    asset_trades = {}
    for t in result.trades:
        asset_trades.setdefault(t.asset, []).append(t)

    print(f"\n{'─' * 40}")
    print("Per-Asset Breakdown")
    print(f"{'─' * 40}")
    for asset, at in sorted(asset_trades.items()):
        avg_pnl = np.mean([t.pnl_pct for t in at])
        wr = len([t for t in at if t.pnl_pct > 0]) / len(at) * 100
        print(f"  {asset}: {len(at)} trades, WR {wr:.0f}%, Avg PnL {avg_pnl:.2f}%")

    # 차트 저장
    try:
        plot_momentum_results(
            data, result, save_path="backtests/dual_momentum_result.png"
        )
    except Exception:
        print("\nNote: Chart generation skipped (no display available)")


if __name__ == "__main__":
    main()
