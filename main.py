from datetime import datetime, timedelta
from pathlib import Path
import time
import pandas as pd
import numpy as np
import pytz
from dhanhq import DhanContext, dhanhq

import config
from utils.security_master import get_symbol_map
from utils.metrics import compute_atr, compute_volatility, compute_trend_score
from bigquery_helper import write_snapshot_records, write_static_metrics   # ← correct

def load_symbols_by_category() -> dict[str, list[str]]:
    """
    Load symbols from every relevant sheet, keeping category so each
    stock can be routed to the right spreadsheet (MidCap / Range /
    SmallCap). Sheet-name matching mirrors the depth-snapshot script.
    """
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


def fetch_history(dhan, security_id: str, retries: int = 2) -> pd.DataFrame:
    """
    Fetch daily OHLCV history for one stock.

    dhan.historical_daily_data() returns the full API envelope —
    {"status": "success", "data": {"close": [...], "open": [...], ...}} —
    not the OHLCV arrays directly, so we must unwrap resp["data"].
    """
    to_dt = datetime.now().date()
    from_dt = to_dt - timedelta(days=config.HISTORY_DAYS)

    for attempt in range(retries + 1):
        try:
            resp = dhan.historical_daily_data(
                security_id=str(security_id),
                exchange_segment="NSE_EQ",
                instrument_type="EQUITY",
                from_date=from_dt.strftime("%Y-%m-%d"),
                to_date=to_dt.strftime("%Y-%m-%d")
            )
            if not resp or resp.get("status") != "success":
                if attempt < retries:
                    time.sleep(1)
                    continue
                return pd.DataFrame()

            data = resp.get("data") or {}
            if not data or "close" not in data or len(data["close"]) == 0:
                return pd.DataFrame()

            return pd.DataFrame({
                "open": data["open"],
                "high": data["high"],
                "low": data["low"],
                "close": data["close"],
                "volume": data.get("volume", [0] * len(data["close"]))
            })
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"  History error {security_id}: {e}")
            return pd.DataFrame()

    return pd.DataFrame()


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Starting full automation for all stocks...")

    dhan = dhanhq(DhanContext(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN))
    symbol_map = get_symbol_map()
    categories = load_symbols_by_category()

    # Flatten to one list for batched quote fetching, but keep category
    # alongside each symbol so we know which category it belongs to.
    valid = []  # (symbol, security_id, category)
    for cat, symbols in categories.items():
        for sym in symbols:
            sid = symbol_map.get(sym)
            if sid:
                valid.append((sym, sid, cat))
            else:
                print(f"  Missing security_id -> {sym}")

    print(f"Processing {len(valid)} stocks with valid Dhan IDs...")

    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    snapshot_time = now_ist.strftime("%Y-%m-%d %H:%M:%S")
    session = "Morning" if now_ist.hour < 10 else "Midday" if now_ist.hour < 13 else "Closing"

    # ---- Live quotes in batches (across all categories at once) ----
    quote_cache = {}
    sids = [sid for _, sid, _ in valid]

    for i in range(0, len(sids), config.BATCH_SIZE_QUOTES):
        batch = sids[i:i + config.BATCH_SIZE_QUOTES]
        try:
            resp = dhan.quote_data(securities={"NSE_EQ": batch})
            data = resp.get("data", resp) if isinstance(resp, dict) else {}
            for sid, q in data.items():
                quote_cache[str(sid)] = q
        except Exception as e:
            print(f"Quote batch error: {e}")
        time.sleep(0.4)

    # ---- Process each stock and collect records ----
    snapshot_records = []
    static_records = []
    fetch_failures = 0
    processed = 0

    for idx, (sym, sid, cat) in enumerate(valid, 1):
        if idx % 100 == 0 or idx == len(valid):
            print(f"[{idx}/{len(valid)}] {sym} ({cat}) | "
                  f"History fetch failures so far: {fetch_failures}")

        q = quote_cache.get(str(sid), {})
        ltp = q.get("last_price") or q.get("LTP") or q.get("ltp")
        volume = q.get("volume") or q.get("total_volume")

        bid = ask = spread = None
        depth = q.get("depth") or q.get("market_depth") or {}
        try:
            if "buy" in depth and depth["buy"]:
                bid = depth["buy"][0].get("price")
            if "sell" in depth and depth["sell"]:
                ask = depth["sell"][0].get("price")
            if bid is not None and ask is not None:
                spread = round(ask - bid, 4)
        except Exception:
            pass

        volatility = atr = atr_pct = avg_vol = None
        hist = fetch_history(dhan, sid)
        if hist.empty:
            fetch_failures += 1
        else:
            volatility = compute_volatility(hist)
            atr_val = compute_atr(hist, config.ATR_PERIOD)
            atr = None if np.isnan(atr_val) else atr_val
            if atr is not None and ltp:
                atr_pct = atr / ltp * 100
            avg_vol = float(hist["volume"].mean()) if not hist.empty else None

        # Collect live snapshot
        snapshot_records.append({
            "Snapshot_Time": now_ist,
            "Session": session,
            "Category": cat,
            "SYMBOL": sym,
            "security_id": str(sid),
            "Bid": bid,
            "Ask": ask,
            "Spread": spread,
            "LTP": ltp,
            "Volume": volume,
        })

        # Collect static metrics
        static_records.append({
            "SYMBOL": sym,
            "security_id": str(sid),
            "Category": cat,
            "last_history_update": now_ist,
            "volatility_6m": round(volatility, 2) if volatility else None,
            "atr_14": round(atr, 2) if atr else None,
            "atr_pct_of_price": round(atr_pct, 2) if atr_pct else None,
            "avg_volume_6m": round(avg_vol, 0) if avg_vol else None,
            "updated_at": now_ist,
        })

        processed += 1

    # ---- Write everything to BigQuery in two efficient batches ----
    print("\nWriting to BigQuery...")
    write_snapshot_records(snapshot_records)
    write_static_metrics(static_records)

    print(f"\n[SUMMARY] History fetch failures: {fetch_failures}/{len(valid)}")
    print(f"Done. Processed {processed}/{len(valid)} stocks → BigQuery.")


if __name__ == "__main__":
    main() 
