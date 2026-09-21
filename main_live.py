"""
Live Bid-Ask snapshot → writes to Airtable
"""
import time
from datetime import datetime
import pytz
import pandas as pd
from dhanhq import DhanContext, dhanhq
import config
from utils.security_master import get_symbol_map
from utils.airtable_helper import upsert_live_records

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
                    df[col].dropna().astype(str).str.strip().str.upper().tolist()
                )
                categories[cat].extend(symbols)
                break

    for cat in categories:
        categories[cat] = sorted(list(set(categories[cat])))
    return categories

def safe_quote(dhan, batch):
    try:
        resp = dhan.quote_data(securities={"NSE_EQ": batch})
        if isinstance(resp, str):
            print(f"  Dhan returned string: {resp[:150]}")
            return {}
        if not isinstance(resp, dict):
            return {}
        data = resp.get("data", resp)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"  Quote exception: {e}")
        return {}

def main():
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    snapshot_time = now.strftime("%Y-%m-%d %H:%M:%S")
    session = "Morning" if now.hour < 10 else "Midday" if now.hour < 13 else "Closing"

    print(f"Live snapshot started at {snapshot_time} ({session})")

    dhan = dhanhq(DhanContext(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN))
    symbol_map = get_symbol_map()
    categories = load_symbols_by_category()

    all_records = []

    for category, symbols in categories.items():
        print(f"\n=== {category} ({len(symbols)} symbols) ===")

        valid = [(sym, symbol_map[sym]) for sym in symbols if sym in symbol_map]
        sids = [sid for _, sid in valid]

        # Fetch quotes
        quote_cache = {}
        for i in range(0, len(sids), config.BATCH_SIZE):
            batch = sids[i:i + config.BATCH_SIZE]
            data = safe_quote(dhan, batch)
            for sid, q in data.items():
                quote_cache[str(sid)] = q
            time.sleep(0.4)

        # Build records
        for sym, sid in valid:
            q = quote_cache.get(str(sid), {})

            ltp = q.get("last_price") or q.get("LTP") or q.get("ltp")
            volume = q.get("volume") or q.get("total_volume")

            bid = ask = None
            depth = q.get("depth") or q.get("market_depth") or {}
            try:
                if "buy" in depth and depth["buy"]:
                    bid = depth["buy"][0].get("price")
                if "sell" in depth and depth["sell"]:
                    ask = depth["sell"][0].get("price")
            except Exception:
                pass

            spread = round(ask - bid, 4) if (bid is not None and ask is not None) else None

            all_records.append({
                "Snapshot_Time": snapshot_time,
                "Session": session,
                "Category": category,
                "SYMBOL": sym,
                "security_id": str(sid),
                "Bid": bid,
                "Ask": ask,
                "Spread": spread,
                "LTP": ltp,
                "Volume": volume,
            })

        print(f"  Collected {len(valid)} rows for {category}")

    if not all_records:
        print("No records to write.")
        return

    print(f"\nWriting {len(all_records)} total records to Airtable...")
    upsert_live_records(all_records)
    print("✓ Successfully written to Airtable")

if __name__ == "__main__":
    main()
