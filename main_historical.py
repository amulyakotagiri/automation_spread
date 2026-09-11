"""
Fetches 6-month history once and updates static metrics
(Volatility, ATR, Avg Volume) in each stock's individual sheet.
"""

import time
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from dhanhq import DhanContext, dhanhq
import config
from utils.security_master import get_symbol_map
from utils.metrics import compute_atr, compute_volatility
from utils.google_sheets import get_client, get_or_create_stock_sheet, update_static_metrics

def load_symbols_by_category():
    xls = pd.ExcelFile(config.SYMBOLS_FILE)
    categories = {
        "MidCap": [],
        "Range_100_1000": [],
        "SmallCap": []
    }

    for sheet_name in xls.sheet_names:
        if "midcap" in sheet_name.lower():
            cat = "MidCap"
        elif "range" in sheet_name.lower():
            cat = "Range_100_1000"
        elif "small" in sheet_name.lower():
            cat = "SmallCap"
        else:
            continue

        df = pd.read_excel(xls, sheet_name=sheet_name, header=1)
        for col in df.columns:
            if str(col).strip().upper() in ["SYMBOL", "SYMBOLS"]:
                symbols = df[col].dropna().astype(str).str.strip().str.upper().tolist()
                categories[cat].extend(symbols)
                break

    for cat in categories:
        categories[cat] = sorted(list(set(categories[cat])))
    return categories

def main():
    print("Starting weekly historical update...")
    dhan = dhanhq(DhanContext(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN))
    symbol_map = get_symbol_map()
    categories = load_symbols_by_category()
    client = get_client()

    for category, symbols in categories.items():
        sheet_id = config.SPREADSHEET_IDS.get(category)
        if not sheet_id:
            print(f"Skipping {category} - no Spreadsheet ID")
            continue

        print(f"\n=== Processing {category} ({len(symbols)} stocks) ===")

        for i, sym in enumerate(symbols, 1):
            sid = symbol_map.get(sym)
            if not sid:
                print(f"  [{i}] {sym} → no security_id")
                continue

            print(f"  [{i}/{len(symbols)}] {sym}")

            to_dt = datetime.now().date()
            from_dt = to_dt - timedelta(days=config.HISTORY_DAYS)
            try:
                data = dhan.historical_daily_data(
                    security_id=str(sid),
                    exchange_segment="NSE_EQ",
                    instrument_type="EQUITY",
                    from_date=from_dt.strftime("%Y-%m-%d"),
                    to_date=to_dt.strftime("%Y-%m-%d")
                )
                if not data or "close" not in data:
                    continue

                hist = pd.DataFrame({
                    "high": data["high"],
                    "low": data["low"],
                    "close": data["close"],
                    "volume": data["volume"]
                })

                volatility = compute_volatility(hist)
                atr = compute_atr(hist, config.ATR_PERIOD)
                avg_vol = hist["volume"].mean()
                atr_pct = (atr / hist["close"].iloc[-1] * 100) if atr and hist["close"].iloc[-1] else None

                ws = get_or_create_stock_sheet(client, sheet_id, sym)
                update_static_metrics(ws, sid, volatility, atr, atr_pct, avg_vol)

            except Exception as e:
                print(f"    Error: {e}")

            time.sleep(0.25)

    print("\nHistorical update completed.")

if __name__ == "__main__":
    main()
