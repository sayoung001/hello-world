"""
Grid Trading Strategy Backtest
==============================
크립토 시장용 그리드 트레이딩 백테스트.
가격 범위 내 격자형 매수/매도 주문으로 변동성 수익 확보.

사용법:
    python backtests/grid_trading.py

필요 패키지:
    pip install pandas numpy matplotlib
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from dataclasses import dataclass, field


@dataclass
class GridTrade:
    time: object
    action: str  # "buy" or "sell"
    price: float
    grid_level: int
    pnl: float = 0.0


@dataclass
class GridBacktestResult:
    trades: list = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown: float = 0.0
    total_trades: int = 0
    grid_pnl_pct: float = 0.0
    net_pnl_pct: float = 0.0
    avg_daily_drawdown: float = 0.0


def generate_ranging_crypto_data(
    days: int = 365,
    initial_price: float = 30000.0,
    volatility: float = 0.02,
    mean_reversion_strength: float = 0.005,
    seed: int = 42,
) -> pd.DataFrame:
    """횡보장 특성의 크립토 데이터 생성."""
    np.random.seed(seed)
    n_candles = days * 24

    price = initial_price
    prices = []
    mean_price = initial_price

    for _ in range(n_candles):
        ret = np.random.normal(0, volatility)
        # 강한 평균회귀로 횡보장 시뮬레이션
        mr_force = -mean_reversion_strength * (price - mean_price) / mean_price
        price *= 1 + ret + mr_force
        prices.append(price)

    prices = np.array(prices)
    dates = pd.date_range("2024-01-01", periods=n_candles, freq="h")

    high = prices * (1 + np.abs(np.random.normal(0, 0.003, n_candles)))
    low = prices * (1 - np.abs(np.random.normal(0, 0.003, n_candles)))

    return pd.DataFrame(
        {
            "open": prices,
            "high": high,
            "low": low,
            "close": prices,
            "volume": np.random.uniform(100, 1000, n_candles),
        },
        index=dates,
    )


def run_grid_backtest(
    df: pd.DataFrame,
    initial_capital: float = 10000.0,
    n_grids: int = 20,
    grid_range_pct: float = 0.20,
    fee_rate: float = 0.001,
) -> GridBacktestResult:
    """Grid Trading 백테스트 실행."""
    mid_price = df["close"].iloc[:100].mean()
    grid_upper = mid_price * (1 + grid_range_pct / 2)
    grid_lower = mid_price * (1 - grid_range_pct / 2)
    grid_size = (grid_upper - grid_lower) / n_grids

    # 그리드 레벨 생성
    grid_levels = [grid_lower + i * grid_size for i in range(n_grids + 1)]

    # 각 그리드에 할당할 자금
    capital_per_grid = initial_capital / n_grids

    # 그리드 상태: 각 레벨에 보유 포지션이 있는지 추적
    # True = 해당 레벨 아래에서 매수하여 보유 중
    grid_holdings = [False] * n_grids
    grid_entry_prices = [0.0] * n_grids

    capital = initial_capital
    total_grid_pnl = 0.0
    trades = []
    equity = []

    for i in range(len(df)):
        row = df.iloc[i]
        price = row["close"]

        # 각 그리드 레벨 확인
        for g in range(n_grids):
            lower_level = grid_levels[g]
            upper_level = grid_levels[g + 1]

            if not grid_holdings[g]:
                # 미보유: 가격이 하단 레벨 이하이면 매수
                if price <= lower_level:
                    qty = capital_per_grid / price
                    cost = qty * price * (1 + fee_rate)
                    if capital >= cost:
                        capital -= cost
                        grid_holdings[g] = True
                        grid_entry_prices[g] = price
                        trades.append(
                            GridTrade(
                                time=df.index[i],
                                action="buy",
                                price=price,
                                grid_level=g,
                            )
                        )
            else:
                # 보유 중: 가격이 상단 레벨 이상이면 매도
                if price >= upper_level:
                    qty = capital_per_grid / grid_entry_prices[g]
                    proceeds = qty * price * (1 - fee_rate)
                    pnl = proceeds - capital_per_grid
                    capital += proceeds
                    total_grid_pnl += pnl
                    grid_holdings[g] = False

                    trades.append(
                        GridTrade(
                            time=df.index[i],
                            action="sell",
                            price=price,
                            grid_level=g,
                            pnl=pnl,
                        )
                    )

        # 미실현 포지션 포함 자산 계산
        unrealized = sum(
            (capital_per_grid / grid_entry_prices[g]) * price
            for g in range(n_grids)
            if grid_holdings[g] and grid_entry_prices[g] > 0
        )
        equity.append(capital + unrealized)

    equity_series = pd.Series(equity, index=df.index)

    # 결과 계산
    result = GridBacktestResult()
    result.trades = trades
    result.equity_curve = equity_series
    result.total_trades = len(trades)

    sell_trades = [t for t in trades if t.action == "sell"]
    if sell_trades:
        wins = [t for t in sell_trades if t.pnl > 0]
        result.win_rate = len(wins) / len(sell_trades) * 100

    result.total_pnl_pct = (equity_series.iloc[-1] / initial_capital - 1) * 100
    result.grid_pnl_pct = total_grid_pnl / initial_capital * 100
    result.net_pnl_pct = result.total_pnl_pct

    # MDD
    peak = equity_series.expanding().max()
    drawdown = (equity_series - peak) / peak * 100
    result.max_drawdown = drawdown.min()

    # 일별 평균 드로다운
    daily_equity = equity_series.resample("D").last().dropna()
    daily_peak = daily_equity.expanding().max()
    daily_dd = (daily_equity - daily_peak) / daily_peak * 100
    result.avg_daily_drawdown = daily_dd.mean()

    return result


def plot_grid_results(
    df: pd.DataFrame, result: GridBacktestResult, save_path: str = None
):
    """그리드 트레이딩 결과 시각화."""
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)

    # 가격 + 매매 포인트
    axes[0].plot(df.index, df["close"], label="Close", linewidth=0.8, color="gray")

    buy_trades = [t for t in result.trades if t.action == "buy"]
    sell_trades = [t for t in result.trades if t.action == "sell"]

    if buy_trades:
        axes[0].scatter(
            [t.time for t in buy_trades],
            [t.price for t in buy_trades],
            marker="^",
            color="green",
            s=10,
            alpha=0.6,
            label=f"Buy ({len(buy_trades)})",
        )
    if sell_trades:
        axes[0].scatter(
            [t.time for t in sell_trades],
            [t.price for t in sell_trades],
            marker="v",
            color="red",
            s=10,
            alpha=0.6,
            label=f"Sell ({len(sell_trades)})",
        )

    axes[0].set_title("Grid Trading - Price & Trades")
    axes[0].legend()

    # 자산 곡선
    axes[1].plot(result.equity_curve.index, result.equity_curve, color="blue")
    axes[1].set_title("Equity Curve")
    axes[1].axhline(y=10000, color="gray", linestyle="--", alpha=0.5)

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150)
        print(f"Chart saved to {save_path}")
    else:
        plt.show()


def main():
    print("=" * 60)
    print("Grid Trading Strategy Backtest")
    print("Market: Crypto (BTC/USDT simulated, ranging)")
    print("=" * 60)

    # 횡보장 데이터 생성
    df = generate_ranging_crypto_data(days=365)
    print(f"\nData: {len(df)} candles ({df.index[0]} ~ {df.index[-1]})")
    print(f"Price range: ${df['close'].min():.0f} ~ ${df['close'].max():.0f}")

    # 백테스트 실행
    result = run_grid_backtest(df, n_grids=20, grid_range_pct=0.20)

    # 결과 출력
    print(f"\n{'─' * 40}")
    print("Performance Summary")
    print(f"{'─' * 40}")
    print(f"Total Trades:        {result.total_trades}")
    print(f"  Buy Orders:        {len([t for t in result.trades if t.action == 'buy'])}")
    print(f"  Sell Orders:       {len([t for t in result.trades if t.action == 'sell'])}")
    print(f"Win Rate:            {result.win_rate:.1f}%")
    print(f"Grid PnL:            {result.grid_pnl_pct:.2f}%")
    print(f"Net PnL:             {result.net_pnl_pct:.2f}%")
    print(f"Max Drawdown:        {result.max_drawdown:.2f}%")
    print(f"Avg Daily Drawdown:  {result.avg_daily_drawdown:.2f}%")

    # 차트 저장
    try:
        plot_grid_results(df, result, save_path="backtests/grid_trading_result.png")
    except Exception:
        print("\nNote: Chart generation skipped (no display available)")


if __name__ == "__main__":
    main()
