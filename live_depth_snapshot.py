"""
Live Bid-Ask / Market Depth snapshot fetcher.

Called repeatedly by depth_snapshot_scheduler.py at each of the day's
target times. The symbol universe and the Dhan client are set up once
when this module is imported, so each snapshot call only does the
fetch + write work — not the Excel/setup overhead.
"""
import time
from datetime import datetime
import pytz
import pandas as pd
from dhanhq import DhanContext, dhanhq

import config
from utils.security_master import get_symbol_map
from bigquery_helper import write_snapshot_records

IST = pytz.timezone("Asia/Kolkata")

# ---- One-time setup (runs once, at import) ----
_dhan = dhanhq(DhanContext(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN))
_symbol_map = get_symbol_map()


def _load_symbols_by_category():
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
                symbols = df[col].dropna().astype(str).str.strip().str.upper().tolist()
                categories[cat].extend(symbols)
                break
    for cat in categories:
        categories[cat] = sorted(set(categories[cat]))
    return categories


_categories = _load_symbols_by_category()
print(f"[SETUP] Loaded {sum(len(v) for v in _categories.values())} symbols "
      f"across {len(_categories)} categories", flush=True)


def _safe_quote(batch):
    try:
        resp = _dhan.quote_data(securities={"NSE_EQ": batch})
        if isinstance(resp, str):
            print(f"  Dhan returned string: {resp[:150]}", flush=True)
            return {}
        if not isinstance(resp, dict):
            return {}
        data = resp.get("data", resp)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"  Quote exception: {e}", flush=True)
        return {}


def take_snapshot(scheduled_time: str = None):
    """
    Fetch one market-depth snapshot for every symbol across all
    categories and write it to BigQuery.

    `scheduled_time` (e.g. "09:15") is the scheduler's target label,
    used only for logging — the timestamp actually recorded is the
    real fetch time, since network/API latency means they can differ
    by a few seconds.
    """
    now = datetime.now(IST)
    snapshot_time_iso = now.isoformat()  # e.g. 2026-09-27T09:15:03+05:30
    session = "Morning" if now.hour < 10 else "Midday" if now.hour < 13 else "Closing"

    label = scheduled_time or now.strftime("%H:%M")
    print(f"Snapshot [{label}] started at {snapshot_time_iso} ({session})", flush=True)

    all_records = []

    for category, symbols in _categories.items():
        valid = [(sym, _symbol_map[sym]) for sym in symbols if sym in _symbol_map]
        sids = [sid for _, sid in valid]

        quote_cache = {}
        for i in range(0, len(sids), config.BATCH_SIZE):
            batch = sids[i:i + config.BATCH_SIZE]
            data = _safe_quote(batch)
            for sid, q in data.items():
                quote_cache[str(sid)] = q
            time.sleep(0.4)

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
                "Snapshot_Time": snapshot_time_iso,
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

        print(f"  [{category}] {len(valid)} symbols fetched", flush=True)

    if not all_records:
        print(f"Snapshot [{label}]: no records to write.", flush=True)
        return

    write_snapshot_records(all_records)
    print(f"Snapshot [{label}] complete — {len(all_records)} rows written.", flush=True)


if __name__ == "__main__":
    # Lets you test a single snapshot manually: python live_depth_snapshot.py
    take_snapshot()
