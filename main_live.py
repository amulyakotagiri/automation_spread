"""
Live Bid-Ask snapshot at the defined timestamps.
Appends one row per stock into its individual sheet.
Spread = Ask - Bid (or Bid - Ask)
"""

import sys
import time
from datetime import datetime
from pathlib import Path
import pytz
import pandas as pd
from dhanhq import DhanContext, dhanhq
import config
from utils.security_master import get_symbol_map
from utils.google_sheets import get_client, get_or_create_stock_sheet, append_live_row

def load_symbols_by_category():
    path = Path(config.SYMBOLS_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Symbols Excel file not found: {path}")

    xls = pd.ExcelFile(path)
    categories = {"MidCap": [], "Range_100_1000": [], "SmallCap": []}

    for sheet_name in xls.sheet_names:
        s_lower = sheet_name.lower()
        if "midcap" in s_lower:
            cat = "MidCap"
        elif "range" in s_lower:
            cat = "Range_100_1000"
        elif "small" in s_lower:
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

def extract_quote_dict(resp) -> dict:
    """
    Extract mapping of security_id -> quote from Dhan quote_data response.
    Handles responses formatted as {'data': {'NSE_EQ': {'123': {...}}}}
    as well as {'data': {'123': {...}}}.
    """
    if not resp or not isinstance(resp, dict):
        return {}

    data = resp.get("data", resp)
    if not isinstance(data, dict):
        return {}

    quotes = {}
    for k, v in data.items():
        if isinstance(v, dict):
            # Check if this is a segment dictionary like 'NSE_EQ': {'1234': {...}}
            has_sid_keys = any(str(sub_k).isdigit() for sub_k in v.keys())
            if has_sid_keys:
                for sid, q in v.items():
                    quotes[str(sid)] = q
            else:
                quotes[str(k)] = v
        else:
            quotes[str(k)] = v
    return quotes

def main():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    snapshot_time = now.strftime("%Y-%m-%d %H:%M:%S")
    session = "Morning" if now.hour < 10 else "Midday" if now.hour < 13 else "Closing"

    print("=" * 60)
    print(f"Live snapshot started at {snapshot_time} ({session})")
    print("=" * 60)

    # Validate environment & secrets
    errors = []
    if not config.DHAN_CLIENT_ID:
        errors.append("DHAN_CLIENT_ID is missing or empty.")
    if not config.DHAN_ACCESS_TOKEN:
        errors.append("DHAN_ACCESS_TOKEN is missing or empty.")
    if not Path(config.GOOGLE_CREDENTIALS_FILE).exists():
        errors.append(f"Google credentials file '{config.GOOGLE_CREDENTIALS_FILE}' does not exist.")

    if errors:
        for err in errors:
            print(f"::error::{err}")
        sys.exit(1)

    dhan = dhanhq(DhanContext(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN))
    symbol_map = get_symbol_map()
    categories = load_symbols_by_category()
    client = get_client()

    for category, symbols in categories.items():
        sheet_id = config.SPREADSHEET_IDS.get(category)
        if not sheet_id:
            print(f"Skipping {category} - no Spreadsheet ID")
            continue

        print(f"\n{'='*20} {category} ({len(symbols)} stocks) {'='*20}")

        try:
            sh = client.open_by_key(sheet_id)
            print(f"Connected: '{sh.title}'")
            worksheet_map = {ws.title: ws for ws in sh.worksheets()}
        except Exception as e:
            print(f"::error::Failed to connect to spreadsheet {category}: {e}")
            continue

        # Prepare valid security IDs
        valid = [(sym, symbol_map[sym]) for sym in symbols if sym in symbol_map]
        sids = [sid for _, sid in valid]
        print(f"Found {len(valid)} valid stocks with Dhan IDs")

        # Fetch quotes in batches
        quote_cache = {}
        for i in range(0, len(sids), config.BATCH_SIZE):
            batch = sids[i:i + config.BATCH_SIZE]
            try:
                resp = dhan.quote_data(securities={"NSE_EQ": batch})
                batch_quotes = extract_quote_dict(resp)
                quote_cache.update(batch_quotes)
            except Exception as e:
                print(f"Quote batch error: {e}")
            time.sleep(0.35)

        print(f"Cached {len(quote_cache)} live quotes from Dhan.")

        # Write to individual stock sheets
        success_count = 0
        error_count = 0

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

            spread = round(ask - bid, 4) if (bid is not None and ask is not None) else None

            try:
                ws = worksheet_map.get(sym)
                if ws is None:
                    ws = get_or_create_stock_sheet(client, sheet_id, sym)
                    worksheet_map[sym] = ws

                append_live_row(ws, snapshot_time, session, bid, ask, spread, ltp, volume)
                success_count += 1
                print(f"  {sym}: LTP={ltp}, Bid={bid}, Ask={ask}, Spread={spread}")
            except Exception as e:
                print(f"  Write error {sym}: {e}")
                error_count += 1

            # Sleep 1.05s to respect Google Sheets write quota (~60 writes/min)
            time.sleep(1.05)

        print(f"\nCompleted {category}: {success_count} appended, {error_count} errors.")

    print("\n" + "=" * 60)
    print("Live snapshot completed.")
    print("=" * 60)

if __name__ == "__main__":
    main()