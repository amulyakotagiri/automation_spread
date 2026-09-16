import os
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
from utils.google_sheets import write_or_append

def load_all_symbols() -> list[str]:
    """Load every unique symbol from all sheets of the Excel you shared."""
    path = Path(config.SYMBOLS_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {path}")

    xls = pd.ExcelFile(path)
    all_symbols = set()

    for sheet in xls.sheet_names:
        if sheet.lower() in ["things to do"]:
            continue
        df = pd.read_excel(xls, sheet_name=sheet, header=1)
        # Handle different possible column names
        for col in df.columns:
            if str(col).strip().upper() in ["SYMBOL", "SYMBOLS"]:
                symbols = df[col].dropna().astype(str).str.strip().str.upper()
                all_symbols.update(symbols.tolist())
                break

    symbols = sorted(list(all_symbols))
    print(f"Loaded {len(symbols)} unique symbols from Excel")
    return symbols

def fetch_history(dhan, security_id: str) -> pd.DataFrame:
    to_dt = datetime.now().date()
    from_dt = to_dt - timedelta(days=config.HISTORY_DAYS)
    try:
        resp = dhan.historical_daily_data(
            security_id=str(security_id),
            exchange_segment="NSE_EQ",
            instrument_type="EQUITY",
            from_date=from_dt.strftime("%Y-%m-%d"),
            to_date=to_dt.strftime("%Y-%m-%d")
        )
        if not resp:
            return pd.DataFrame()
        data = resp.get("data", resp) if isinstance(resp, dict) else resp
        if not isinstance(data, dict):
            return pd.DataFrame()
        norm = {str(k).lower(): v for k, v in data.items()}
        if "close" not in norm or not norm["close"]:
            return pd.DataFrame()
        return pd.DataFrame({
            "open": norm.get("open", []),
            "high": norm.get("high", []),
            "low": norm.get("low", []),
            "close": norm.get("close", []),
            "volume": norm.get("volume", [])
        })
    except Exception as e:
        print(f"  History error {security_id}: {e}")
        return pd.DataFrame()

def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Starting full automation for all stocks...")

    dhan = dhanhq(DhanContext(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN))
    symbol_map = get_symbol_map()
    symbols = load_all_symbols()

    # Build security_id list
    valid = []
    for sym in symbols:
        sid = symbol_map.get(sym)
        if sid:
            valid.append((sym, sid))
        else:
            print(f"  Missing security_id → {sym}")

    print(f"Processing {len(valid)} stocks with valid Dhan IDs...")

    results = []
    ist = pytz.timezone("Asia/Kolkata")
    snapshot_time = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")

    # ---- Live quotes in batches ----
    quote_cache = {}
    sids = [sid for _, sid in valid]

    for i in range(0, len(sids), config.BATCH_SIZE_QUOTES):
        batch = sids[i:i + config.BATCH_SIZE_QUOTES]
        try:
            resp = dhan.quote_data(securities={"NSE_EQ": batch})
            # Adjust parsing according to actual response structure
            data = resp.get("data", resp) if isinstance(resp, dict) else {}
            for sid, q in data.items():
                quote_cache[str(sid)] = q
        except Exception as e:
            print(f"Quote batch error: {e}")
        time.sleep(0.4)

    # ---- Process each stock ----
    for idx, (sym, sid) in enumerate(valid, 1):
        print(f"[{idx}/{len(valid)}] {sym}")

        row = {
            "Snapshot_Time": snapshot_time,
            "SYMBOL": sym,
            "security_id": sid,
            "LTP": None, "Bid": None, "Ask": None, "Spread": None,
            "Volume": None,
            "Volatility_Ann_%": None,
            "ATR_14": None, "ATR_%": None,
            "Avg_Volume_6M": None, "Rel_Volume": None,
            "Trend_Score": None,
            "Return_1M_%": None, "Return_3M_%": None,
            "Error": None
        }

        # Live data
        q = quote_cache.get(str(sid), {})
        row["LTP"] = q.get("last_price") or q.get("LTP") or q.get("ltp")
        # Depth parsing – inspect real response once and adjust
        depth = q.get("depth") or q.get("market_depth") or {}
        try:
            if "buy" in depth and depth["buy"]:
                row["Bid"] = depth["buy"][0].get("price")
            if "sell" in depth and depth["sell"]:
                row["Ask"] = depth["sell"][0].get("price")
            if row["Bid"] is not None and row["Ask"] is not None:
                row["Spread"] = round(row["Ask"] - row["Bid"], 4)
        except Exception:
            pass
        row["Volume"] = q.get("volume") or q.get("total_volume")

        # Historical metrics
        hist = fetch_history(dhan, sid)
        if not hist.empty:
            row["Volatility_Ann_%"] = round(compute_volatility(hist), 2)
            atr = compute_atr(hist, config.ATR_PERIOD)
            if not np.isnan(atr):
                row["ATR_14"] = round(atr, 2)
                if row["LTP"]:
                    row["ATR_%"] = round(atr / row["LTP"] * 100, 2)

            row["Avg_Volume_6M"] = int(hist["volume"].mean())
            if row["Volume"] and row["Avg_Volume_6M"]:
                row["Rel_Volume"] = round(row["Volume"] / row["Avg_Volume_6M"], 2)

            row["Trend_Score"] = compute_trend_score(hist, row["LTP"])

            if len(hist) >= 22:
                row["Return_1M_%"] = round((hist["close"].iloc[-1] / hist["close"].iloc[-22] - 1) * 100, 2)
            if len(hist) >= 66:
                row["Return_3M_%"] = round((hist["close"].iloc[-1] / hist["close"].iloc[-66] - 1) * 100, 2)

        results.append(row)
        time.sleep(0.12)

    df = pd.DataFrame(results)

    # Save to Google Sheets
    write_or_append(df, worksheet_name="Latest", clear=True)
    write_or_append(df, worksheet_name="Historical", clear=False)

    print(f"\nDone. Processed {len(df)} stocks.")
    print(f"Success (no Error): {df['Error'].isna().sum()}")

if __name__ == "__main__":
    main()