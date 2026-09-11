import pandas as pd
from pathlib import Path

def get_symbol_map() -> dict:
    cache = Path("data/security_master.csv")
    if cache.exists():
        df = pd.read_csv(cache)
    else:
        print("Downloading Dhan Security Master...")
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        df = pd.read_csv(url)
        df = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "EQUITY")]
        df = df[["SEM_SMST_SECURITY_ID", "SEM_TRADING_SYMBOL"]].copy()
        df.columns = ["security_id", "symbol"]
        df["security_id"] = df["security_id"].astype(str)
        df["symbol"] = df["symbol"].str.upper().str.strip()
        cache.parent.mkdir(exist_ok=True)
        df.to_csv(cache, index=False)

    return dict(zip(df["symbol"], df["security_id"]))