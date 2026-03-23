"""
Mean Reversion Strategy Backtest (Bollinger Bands + RSI)
=======================================================
크립토 시장용 평균회귀 전략 백테스트.
볼린저밴드 이탈 + RSI 과매수/과매도 신호를 결합.

사용법:
    python backtests/mean_reversion_bb_rsi.py

필요 패키지:
    pip install pandas numpy matplotlib
"""

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from dataclasses import dataclass, field


@dataclass
class TradeResult:
    entry_time: object
    exit_time: object
    direction: str  # "long" or "short"
    entry_price: float
    exit_price: float
    pnl_pct: float
    pnl_amount: float


@dataclass
class BacktestResult:
    trades: list = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    win_rate: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    total_trades: int = 0
    avg_trade_pnl: float = 0.0
    profit_factor: float = 0.0


def generate_crypto_data(
    days: int = 730,
    initial_price: float = 30000.0,
    volatility: float = 0.03,
    seed: int = 42,
) -> pd.DataFrame:
    """시뮬레이션된 크립토 OHLCV 데이터 생성 (BTC/USDT 유사)."""
    np.random.seed(seed)
    n_candles = days * 24  # 1시간봉 기준

    # 랜덤워크 + 평균회귀 성분 (크립토 특성 반영)
    returns = np.random.normal(0, volatility, n_candles)
    # 약간의 평균회귀 성분 추가
    price = initial_price
    prices = [price]
    mean_price = initial_price

    for r in returns:
        mean_reversion_force = -0.001 * (price - mean_price) / mean_price
        price *= 1 + r + mean_reversion_force
        prices.append(price)
        mean_price = 0.999 * mean_price + 0.001 * price

    prices = np.array(prices[:-1])
    dates = pd.date_range("2024-01-01", periods=n_candles, freq="h")

    high = prices * (1 + np.abs(np.random.normal(0, 0.005, n_candles)))
    low = prices * (1 - np.abs(np.random.normal(0, 0.005, n_candles)))
    volume = np.random.uniform(100, 1000, n_candles)

    return pd.DataFrame(
        {"open": prices, "high": high, "low": low, "close": prices, "volume": volume},
        index=dates,
    )


def compute_indicators(
    df: pd.DataFrame,
    bb_period: int = 20,
    bb_std: float = 2.0,
    rsi_period: int = 14,
    atr_period: int = 14,
) -> pd.DataFrame:
    """볼린저밴드, RSI, ATR 지표 계산."""
    df = df.copy()

    # Bollinger Bands
    df["bb_mid"] = df["close"].rolling(bb_period).mean()
    bb_std_val = df["close"].rolling(bb_period).std()
    df["bb_upper"] = df["bb_mid"] + bb_std * bb_std_val
    df["bb_lower"] = df["bb_mid"] - bb_std * bb_std_val

    # RSI
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ATR
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = true_range.rolling(atr_period).mean()

    return df


def run_backtest(
    df: pd.DataFrame,
    initial_capital: float = 10000.0,
    position_size_pct: float = 0.02,
    rsi_oversold: float = 30.0,
    rsi_overbought: float = 70.0,
    atr_sl_multiplier: float = 2.0,
    fee_rate: float = 0.001,
) -> BacktestResult:
    """Mean Reversion 전략 백테스트 실행."""
    df = compute_indicators(df)
    df = df.dropna()

    capital = initial_capital
    position = 0.0
    entry_price = 0.0
    entry_time = None
    direction = None
    trades = []
    equity = []

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]

        # 포지션 없을 때: 진입 신호 확인
        if position == 0:
            # Long 진입: 가격이 하단밴드 아래 + RSI 과매도
            if prev["close"] < prev["bb_lower"] and prev["rsi"] < rsi_oversold:
                risk_amount = capital * position_size_pct
                stop_distance = prev["atr"] * atr_sl_multiplier
                if stop_distance > 0:
                    position = risk_amount / stop_distance
                    entry_price = row["open"]
                    entry_time = df.index[i]
                    direction = "long"
                    capital -= position * entry_price * (1 + fee_rate)

            # Short 진입: 가격이 상단밴드 위 + RSI 과매수
            elif prev["close"] > prev["bb_upper"] and prev["rsi"] > rsi_overbought:
                risk_amount = capital * position_size_pct
                stop_distance = prev["atr"] * atr_sl_multiplier
                if stop_distance > 0:
                    position = risk_amount / stop_distance
                    entry_price = row["open"]
                    entry_time = df.index[i]
                    direction = "short"
                    capital += position * entry_price * (1 - fee_rate)

        # 포지션 있을 때: 청산 신호 확인
        else:
            exit_signal = False
            exit_price = row["close"]
            sl_price = 0.0

            if direction == "long":
                sl_price = entry_price - prev["atr"] * atr_sl_multiplier
                # 중심선 복귀 또는 스탑로스
                if row["close"] >= row["bb_mid"] or row["low"] <= sl_price:
                    if row["low"] <= sl_price:
                        exit_price = sl_price
                    exit_signal = True

            elif direction == "short":
                sl_price = entry_price + prev["atr"] * atr_sl_multiplier
                if row["close"] <= row["bb_mid"] or row["high"] >= sl_price:
                    if row["high"] >= sl_price:
                        exit_price = sl_price
                    exit_signal = True

            if exit_signal:
                if direction == "long":
                    pnl_amount = position * (exit_price - entry_price) * (1 - fee_rate)
                    capital += position * exit_price * (1 - fee_rate)
                else:
                    pnl_amount = position * (entry_price - exit_price) * (1 - fee_rate)
                    capital -= position * exit_price * (1 + fee_rate)

                pnl_pct = (exit_price - entry_price) / entry_price * 100
                if direction == "short":
                    pnl_pct = -pnl_pct

                trades.append(
                    TradeResult(
                        entry_time=entry_time,
                        exit_time=df.index[i],
                        direction=direction,
                        entry_price=entry_price,
                        exit_price=exit_price,
                        pnl_pct=pnl_pct,
                        pnl_amount=pnl_amount,
                    )
                )
                position = 0.0
                direction = None

        # 현재 자산 가치 계산
        if position > 0 and direction == "long":
            equity.append(capital + position * row["close"])
        elif position > 0 and direction == "short":
            equity.append(capital - position * row["close"] + 2 * position * entry_price)
        else:
            equity.append(capital)

    equity_series = pd.Series(equity, index=df.index[1:])

    # 성과 지표 계산
    result = BacktestResult()
    result.trades = trades
    result.equity_curve = equity_series
    result.total_trades = len(trades)

    if trades:
        wins = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct <= 0]
        result.win_rate = len(wins) / len(trades) * 100

        result.total_pnl_pct = (equity_series.iloc[-1] / initial_capital - 1) * 100
        result.avg_trade_pnl = np.mean([t.pnl_pct for t in trades])

        gross_profit = sum(t.pnl_amount for t in wins) if wins else 0
        gross_loss = abs(sum(t.pnl_amount for t in losses)) if losses else 1
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # MDD 계산
    peak = equity_series.expanding().max()
    drawdown = (equity_series - peak) / peak * 100
    result.max_drawdown = drawdown.min()

    # Sharpe Ratio (연환산)
    returns = equity_series.pct_change().dropna()
    if returns.std() > 0:
        result.sharpe_ratio = returns.mean() / returns.std() * np.sqrt(365 * 24)

    return result


def plot_results(df: pd.DataFrame, result: BacktestResult, save_path: str = None):
    """백테스트 결과 시각화."""
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

    df_plot = compute_indicators(df).dropna()

    # 가격 + 볼린저밴드
    axes[0].plot(df_plot.index, df_plot["close"], label="Close", linewidth=0.8)
    axes[0].plot(df_plot.index, df_plot["bb_upper"], "r--", alpha=0.5, label="BB Upper")
    axes[0].plot(df_plot.index, df_plot["bb_lower"], "g--", alpha=0.5, label="BB Lower")
    axes[0].fill_between(
        df_plot.index, df_plot["bb_lower"], df_plot["bb_upper"], alpha=0.1
    )
    axes[0].set_title("Price & Bollinger Bands")
    axes[0].legend(loc="upper left")

    # RSI
    axes[1].plot(df_plot.index, df_plot["rsi"], label="RSI", color="purple")
    axes[1].axhline(y=70, color="r", linestyle="--", alpha=0.5)
    axes[1].axhline(y=30, color="g", linestyle="--", alpha=0.5)
    axes[1].set_title("RSI")
    axes[1].set_ylim(0, 100)

    # 자산 곡선
    axes[2].plot(result.equity_curve.index, result.equity_curve, label="Equity", color="blue")
    axes[2].set_title("Equity Curve")
    axes[2].legend()

    plt.tight_layout()
    if save_path:
        plt.savefig(save_path, dpi=150)
        print(f"Chart saved to {save_path}")
    else:
        plt.show()


def main():
    print("=" * 60)
    print("Mean Reversion Strategy Backtest (BB + RSI)")
    print("Market: Crypto (BTC/USDT simulated)")
    print("=" * 60)

    # 데이터 생성 (2년치 1시간봉)
    df = generate_crypto_data(days=730)
    print(f"\nData: {len(df)} candles ({df.index[0]} ~ {df.index[-1]})")

    # 백테스트 실행
    result = run_backtest(df)

    # 결과 출력
    print(f"\n{'─' * 40}")
    print("Performance Summary")
    print(f"{'─' * 40}")
    print(f"Total Trades:    {result.total_trades}")
    print(f"Win Rate:        {result.win_rate:.1f}%")
    print(f"Total PnL:       {result.total_pnl_pct:.2f}%")
    print(f"Avg Trade PnL:   {result.avg_trade_pnl:.2f}%")
    print(f"Max Drawdown:    {result.max_drawdown:.2f}%")
    print(f"Sharpe Ratio:    {result.sharpe_ratio:.2f}")
    print(f"Profit Factor:   {result.profit_factor:.2f}")

    # 매매 방향별 통계
    longs = [t for t in result.trades if t.direction == "long"]
    shorts = [t for t in result.trades if t.direction == "short"]
    print(f"\nLong Trades:     {len(longs)}")
    print(f"Short Trades:    {len(shorts)}")

    if longs:
        long_wr = len([t for t in longs if t.pnl_pct > 0]) / len(longs) * 100
        print(f"Long Win Rate:   {long_wr:.1f}%")
    if shorts:
        short_wr = len([t for t in shorts if t.pnl_pct > 0]) / len(shorts) * 100
        print(f"Short Win Rate:  {short_wr:.1f}%")

    # 차트 저장
    try:
        plot_results(df, result, save_path="backtests/mean_reversion_result.png")
    except Exception:
        print("\nNote: Chart generation skipped (no display available)")


if __name__ == "__main__":
    main()
