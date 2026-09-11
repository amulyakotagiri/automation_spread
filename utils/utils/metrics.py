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