from datetime import datetime, timedelta
from pathlib import Path
import time
import pandas as pd
import numpy as np
import pytz
from fyers_apiv3 import fyersModel

import config
from utils.security_master import get_symbol_map
from utils.metrics import compute_atr, compute_volatility, compute_trend_score
from bigquery_helper import write_snapshot_records, write_static_metrics


def load_symbols_by_category() -> dict[str, list[str]]:
    path = Path(config.SYMBOLS_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {path}")

    xls = pd.ExcelFile(path)
    categories = {"MidCap": [], "Range_100_1000": [], "SmallCap": []}

    for sheet in xls.sheet_names:
        lower = sheet.lower()
        if "midcap" in lower:
            cat = "MidCap"
        elif "range" in lower:
            cat = "Range_100_1000"
        elif "small" in lower:
            cat = "SmallCap"
        else:
            continue

        df = pd.read_excel(xls, sheet_name=sheet, header=1)
        for col in df.columns:
            if str(col).strip().upper() in ["SYMBOL", "SYMBOLS"]:
                symbols = df[col].dropna().astype(str).str.strip().str.upper().tolist()
                categories[cat].extend(symbols)
                break

    for cat in categories:
        categories[cat] = sorted(set(categories[cat]))
        print(f"Loaded {len(categories[cat])} unique symbols for {cat}")

    return categories


def to_fyers_symbol(symbol: str) -> str:
    """Convert RELIANCE → NSE:RELIANCE-EQ"""
    return f"NSE:{symbol}-EQ"


def fetch_history(fyers, symbol: str, retries: int = 2) -> pd.DataFrame:
    """Fetch daily OHLCV history using Fyers."""
    to_dt = datetime.now().date()
    from_dt = to_dt - timedelta(days=config.HISTORY_DAYS)

    data = {
        "symbol": to_fyers_symbol(symbol),
        "resolution": "D",
        "date_format": "1",
        "range_from": from_dt.strftime("%Y-%m-%d"),
        "range_to": to_dt.strftime("%Y-%m-%d"),
        "cont_flag": "1"
    }

    for attempt in range(retries + 1):
        try:
            resp = fyers.history(data=data)
            if resp.get("s") != "ok" or "candles" not in resp:
                if attempt < retries:
                    time.sleep(1)
                    continue
                return pd.DataFrame()

            candles = resp["candles"]
            if not candles:
                return pd.DataFrame()

            df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
            return df[["open", "high", "low", "close", "volume"]]
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"  History error {symbol}: {e}")
            return pd.DataFrame()

    return pd.DataFrame()


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Starting full automation with Fyers...")
    print("Token starts with:", str(config.FYERS_ACCESS_TOKEN)[:30] if config.FYERS_ACCESS_TOKEN else "None")
    print("FYERS_APP_ID present:", bool(config.FYERS_APP_ID))
    print("FYERS_ACCESS_TOKEN present:", bool(config.FYERS_ACCESS_TOKEN))
    print("Client ID being used:", config.FYERS_APP_ID)
    print("Token length:", len(config.FYERS_ACCESS_TOKEN) if config.FYERS_ACCESS_TOKEN else 0)

    # Initialize Fyers
    # Try with AppID:Token format
    combined_token = f"{config.FYERS_APP_ID}:{config.FYERS_ACCESS_TOKEN}"

    fyers = fyersModel.FyersModel(
        client_id=config.FYERS_APP_ID,
        token=combined_token,
        is_async=False,
        log_path=""
    )
    # ---- TEMPORARY SINGLE SYMBOL TEST ----
    print("\n----- TEMPORARY TEST -----")
    print("Testing with single symbol NSE:RELIANCE-EQ ...")
    test_data = {"symbols": "NSE:RELIANCE-EQ"}
    resp = fyers.quotes(data=test_data)
    print("Using combined token starts with:", combined_token[:40])
    print("Single symbol response:", resp)
    print("-----------------------------\n")

    symbol_map = get_symbol_map()
    categories = load_symbols_by_category()

    valid = []  # (symbol, category)
    for cat, symbols in categories.items():
        for sym in symbols:
            valid.append((sym, cat))

    print(f"Processing {len(valid)} stocks...")

    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    session = "Morning" if now_ist.hour < 10 else "Midday" if now_ist.hour < 13 else "Closing"

    # ---- Live quotes in batches (currently disabled for testing) ----
    quote_cache = {}
    # (We skip the real batch quotes for now until the single test works)

    print(f"Total quotes received: {len(quote_cache)}")

    # ---- Process each stock ----
    snapshot_records = []
    static_records = []
    fetch_failures = 0
    processed = 0

    for idx, (sym, cat) in enumerate(valid, 1):
        if idx % 100 == 0 or idx == len(valid):
            print(f"[{idx}/{len(valid)}] {sym} ({cat}) | History failures: {fetch_failures}")

        q = quote_cache.get(sym, {})
        ltp = q.get("lp") or q.get("ltp")
        volume = q.get("volume") or q.get("v")
        bid = q.get("bid")
        ask = q.get("ask")
        spread = None
        if bid is not None and ask is not None:
            try:
                spread = round(float(ask) - float(bid), 4)
            except:
                pass

        # History + metrics
        volatility = atr = atr_pct = avg_vol = None
        hist = fetch_history(fyers, sym)
        if hist.empty:
            fetch_failures += 1
        else:
            volatility = compute_volatility(hist)
            atr_val = compute_atr(hist, config.ATR_PERIOD)
            atr = None if np.isnan(atr_val) else atr_val
            if atr is not None and ltp:
                atr_pct = atr / float(ltp) * 100
            avg_vol = float(hist["volume"].mean()) if not hist.empty else None

        security_id = symbol_map.get(sym, "")

        snapshot_records.append({
            "Snapshot_Time": now_ist,
            "Session": session,
            "Category": cat,
            "SYMBOL": sym,
            "security_id": str(security_id),
            "Bid": bid,
            "Ask": ask,
            "Spread": spread,
            "LTP": ltp,
            "Volume": volume,
        })

        static_records.append({
            "SYMBOL": sym,
            "security_id": str(security_id),
            "Category": cat,
            "last_history_update": now_ist,
            "volatility_6m": round(volatility, 2) if volatility else None,
            "atr_14": round(atr, 2) if atr else None,
            "atr_pct_of_price": round(atr_pct, 2) if atr_pct else None,
            "avg_volume_6m": round(avg_vol, 0) if avg_vol else None,
            "updated_at": now_ist,
        })

        processed += 1

    # ---- Write to BigQuery ----
    print("\nWriting to BigQuery...")
    write_snapshot_records(snapshot_records)
    write_static_metrics(static_records)

    print(f"\n[SUMMARY] History fetch failures: {fetch_failures}/{len(valid)}")
    print(f"Done. Processed {processed}/{len(valid)} stocks → BigQuery.")


if __name__ == "__main__":
    main()
