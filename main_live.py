"""
Live Bid-Ask snapshot.
Appends one row per stock into its individual sheet.
Spread = Ask - Bid
"""
import time
from datetime import datetime
import pytz
import pandas as pd
from dhanhq import DhanContext, dhanhq
import config
from utils.security_master import get_symbol_map
from utils.google_sheets import get_client, get_or_create_stock_sheet, append_live_row

def load_symbols_by_category():
    xls = pd.ExcelFile(config.SYMBOLS_FILE)
    categories = {"MidCap": [], "Range_100_1000": [], "SmallCap": []}

    for sheet_name in xls.sheet_names:
        lower = sheet_name.lower()
        if "midcap" in lower:
            cat = "MidCap"
        elif "range" in lower:
            cat = "Range_100_1000"
        elif "small" in lower:
            cat = "SmallCap"
        else:
            continue

        df = pd.read_excel(xls, sheet_name=sheet_name, header=1)
        for col in df.columns:
            if str(col).strip().upper() in ["SYMBOL", "SYMBOLS"]:
                symbols = (
                    df[col]
                    .dropna()
                    .astype(str)
                    .str.strip()
                    .str.upper()
                    .tolist()
                )
                categories[cat].extend(symbols)
                break

    for cat in categories:
        categories[cat] = sorted(list(set(categories[cat])))
    return categories

def main():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    snapshot_time = now.strftime("%Y-%m-%d %H:%M:%S")
    session = "Morning" if now.hour < 10 else "Midday" if now.hour < 13 else "Closing"

    print(f"Live snapshot started at {snapshot_time} ({session})")

    dhan = dhanhq(DhanContext(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN))
    symbol_map = get_symbol_map()
    categories = load_symbols_by_category()
    client = get_client()

    for category, symbols in categories.items():
        sheet_id = config.SPREADSHEET_IDS.get(category)
        if not sheet_id:
            print(f"Skipping {category} — no spreadsheet ID")
            continue

        print(f"\n=== {category} ===")

        valid = [(sym, symbol_map[sym]) for sym in symbols if sym in symbol_map]
        sids = [sid for _, sid in valid]

        # Batch quotes
        quote_cache = {}
        for i in range(0, len(sids), config.BATCH_SIZE):
            batch = sids[i : i + config.BATCH_SIZE]
            try:
                resp = dhan.quote_data(securities={"NSE_EQ": batch})
                data = resp.get("data", resp) if isinstance(resp, dict) else {}
                for sid, q in data.items():
                    quote_cache[str(sid)] = q
            except Exception as e:
                print(f"Quote error: {e}")
            time.sleep(0.35)

        # Write to individual sheets
        for sym, sid in valid:
            q = quote_cache.get(str(sid), {})
            bid = None
            ask = None
            ltp = q.get("last_price") or q.get("LTP") or q.get("ltp")
            volume = q.get("volume") or q.get("total_volume")

            depth = q.get("depth") or q.get("market_depth") or {}
            try:
                if "buy" in depth and depth["buy"]:
                    bid = depth["buy"][0].get("price")
                if "sell" in depth and depth["sell"]:
                    ask = depth["sell"][0].get("price")
            except Exception:
                pass

            # Correct spread calculation
            spread = round(ask - bid, 4) if (bid is not None and ask is not None) else None

            try:
                ws = get_or_create_stock_sheet(client, sheet_id, sym)
                append_live_row(ws, snapshot_time, session, bid, ask, spread, ltp, volume)
            except Exception as e:
                print(f"  Write error {sym}: {e}")

            time.sleep(0.08)

    print("Live snapshot completed.")

if __name__ == "__main__":
    main()
