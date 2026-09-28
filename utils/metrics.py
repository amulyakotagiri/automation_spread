import numpy as np
import pandas as pd

def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return np.nan
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])

def compute_volatility(df: pd.DataFrame) -> float:
    if len(df) < 20:
        return np.nan
    rets = np.log(df["close"] / df["close"].shift(1)).dropna()
    return float(rets.std() * np.sqrt(252) * 100)


def compute_trend_score(df: pd.DataFrame, short: int = 20, long: int = 50) -> float:
    """
    Simple trend score based on SMA crossover + slope.
    Returns a value roughly between -100 and +100.
    Positive = uptrend, Negative = downtrend.
    """
    if len(df) < long + 5:
        return np.nan

    close = df["close"]
    sma_short = close.rolling(short).mean()
    sma_long = close.rolling(long).mean()

    # Distance between short and long SMA (normalized)
    diff = (sma_short.iloc[-1] - sma_long.iloc[-1]) / sma_long.iloc[-1] * 100

    # Slope of the short SMA over last 5 periods
    slope = (sma_short.iloc[-1] - sma_short.iloc[-6]) / sma_short.iloc[-6] * 100

    score = 0.7 * diff + 0.3 * slope
    return float(np.clip(score, -100, 100))
