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

def compute_trend_score(hist: pd.DataFrame, ltp: float = None) -> float:
    """Compute trend score based on price vs 20-day and 50-day moving averages."""
    if len(hist) < 50:
        return np.nan
    close = hist["close"]
    curr = ltp if (ltp is not None and not np.isnan(ltp)) else close.iloc[-1]
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    score = 0
    if curr > sma20:
        score += 1
    if curr > sma50:
        score += 1
    if sma20 > sma50:
        score += 1
    return float(score)

def safe_val(val, decimals=2, is_int=False):
    """Safely format numbers for Google Sheets, converting NaN/None to empty string."""
    if val is None:
        return ""
    if isinstance(val, (float, int, np.number)):
        if np.isnan(val) or np.isinf(val):
            return ""
        if is_int:
            return int(val)
        return round(float(val), decimals)
    return str(val)