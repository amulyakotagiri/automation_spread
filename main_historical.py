"""
Fetches 6-month history once and updates static metrics
(Volatility, ATR, Avg Volume) in each stock's individual sheet.
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
from dhanhq import DhanContext, dhanhq
import config
from utils.security_master import get_symbol_map
from utils.metrics import compute_atr, compute_volatility
from utils.google_sheets import get_client, save_stock_historical

def load_symbols_by_category():
    path = Path(config.SYMBOLS_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Symbols Excel file not found: {path}")

    xls = pd.ExcelFile(path)
    categories = {
        "MidCap": [],
        "Range_100_1000": [],
        "SmallCap": []
    }

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

def fetch_stock_history(dhan, sid: str, from_dt, to_dt):
    """
    Fetch daily historical OHLC data from Dhan and return a DataFrame.
    Correctly unpacks DhanHQ response structure: {'status': 'success', 'data': {...}}
    """
    try:
        resp = dhan.historical_daily_data(
            security_id=str(sid),
            exchange_segment="NSE_EQ",
            instrument_type="EQUITY",
            from_date=from_dt.strftime("%Y-%m-%d"),
            to_date=to_dt.strftime("%Y-%m-%d")
        )
    except Exception as e:
        return None, f"Dhan API request exception: {e}"

    if not resp:
        return None, "Empty response from Dhan API"

    if isinstance(resp, dict):
        # Check if Dhan returned an explicit failure status
        if resp.get("status", "").lower() == "failure":
            remarks = resp.get("remarks") or resp.get("data") or "Unknown API failure"
            return None, f"Dhan API failure: {remarks}"

        # Unpack data if wrapped inside 'data' key
        payload = resp.get("data", resp)
    else:
        payload = resp

    if not isinstance(payload, dict):
        return None, f"Unexpected payload type: {type(payload)}"

    # Normalize keys to lowercase
    norm = {str(k).lower(): v for k, v in payload.items()}

    if "close" not in norm or not norm["close"]:
        return None, "No candle data ('close' missing or empty) in response"

    try:
        hist = pd.DataFrame({
            "high": norm.get("high", []),
            "low": norm.get("low", []),
            "close": norm.get("close", []),
            "volume": norm.get("volume", [])
        })
        return hist, None
    except Exception as e:
        return None, f"DataFrame construction error: {e}"

def main():
    print("=" * 60)
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Starting Weekly Historical Metrics Update")
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

    configured_sheets = {k: v for k, v in config.SPREADSHEET_IDS.items() if v}
    if not configured_sheets:
        print("::error::No spreadsheet IDs configured in SHEET_ID_MIDCAP, SHEET_ID_RANGE, or SHEET_ID_SMALLCAP!")
        sys.exit(1)

    print(f"Configured spreadsheets: {list(configured_sheets.keys())}")

    # Initialize clients
    dhan = dhanhq(DhanContext(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN))
    symbol_map = get_symbol_map()
    categories = load_symbols_by_category()
    client = get_client()

    to_dt = datetime.now().date()
    from_dt = to_dt - timedelta(days=config.HISTORY_DAYS)
    print(f"Fetching history from {from_dt} to {to_dt} ({config.HISTORY_DAYS} days)")

    total_success = 0
    total_skipped = 0
    total_errors = 0

    for category, symbols in categories.items():
        sheet_id = config.SPREADSHEET_IDS.get(category)
        if not sheet_id:
            print(f"\nSkipping {category} - no Spreadsheet ID configured.")
            continue

        print(f"\n{'='*20} Processing {category} ({len(symbols)} stocks) {'='*20}")
        print(f"Connecting to Google Spreadsheet ID: {sheet_id}...")

        try:
            sh = client.open_by_key(sheet_id)
            print(f"Connected: '{sh.title}'")
            # Cache all existing worksheets once to save read calls
            worksheet_map = {ws.title: ws for ws in sh.worksheets()}
            print(f"Found {len(worksheet_map)} existing worksheets in workbook.")
        except Exception as e:
            print(f"::error::Failed to connect to spreadsheet {category} ({sheet_id}): {e}")
            continue

        cat_success = 0
        cat_skipped = 0
        cat_errors = 0

        for i, sym in enumerate(symbols, 1):
            sid = symbol_map.get(sym)
            if not sid:
                print(f"  [{i}/{len(symbols)}] {sym} -> SKIPPED (No Dhan Security ID found in master)")
                cat_skipped += 1
                continue

            hist, err = fetch_stock_history(dhan, sid, from_dt, to_dt)
            if hist is None or hist.empty:
                print(f"  [{i}/{len(symbols)}] {sym} (ID: {sid}) -> SKIPPED ({err})")
                cat_skipped += 1
                time.sleep(0.1)
                continue

            volatility = compute_volatility(hist)
            atr = compute_atr(hist, config.ATR_PERIOD)
            avg_vol = hist["volume"].mean() if "volume" in hist else None
            last_close = hist["close"].iloc[-1] if not hist["close"].empty else None
            atr_pct = (atr / last_close * 100) if (atr and not np.isnan(atr) and last_close) else None

            is_new = False
            try:
                is_new = save_stock_historical(
                    sh=sh,
                    worksheet_map=worksheet_map,
                    symbol=sym,
                    security_id=sid,
                    volatility=volatility,
                    atr=atr,
                    atr_pct=atr_pct,
                    avg_vol=avg_vol
                )
                cat_success += 1
                vol_str = f"{volatility:.2f}%" if (volatility is not None and not np.isnan(volatility)) else "N/A"
                atr_str = f"{atr:.2f}" if (atr is not None and not np.isnan(atr)) else "N/A"
                status_tag = "CREATED & UPDATED" if is_new else "UPDATED"
                print(f"  [{i}/{len(symbols)}] {sym} -> {status_tag} (Vol: {vol_str}, ATR: {atr_str})")
            except Exception as e:
                print(f"  [{i}/{len(symbols)}] {sym} -> ERROR updating sheet: {e}")
                cat_errors += 1

            # Sleep 2.0s for newly created sheets (header + freeze), or 1.1s for simple B2:B7 updates
            time.sleep(2.0 if is_new else 1.1)

        print(f"\nSummary for {category}:")
        print(f"  Successfully updated: {cat_success}")
        print(f"  Skipped:              {cat_skipped}")
        print(f"  Errors:               {cat_errors}")

        total_success += cat_success
        total_skipped += cat_skipped
        total_errors += cat_errors

    print("\n" + "=" * 60)
    print("HISTORICAL METRICS UPDATE COMPLETED")
    print(f"Total Updated: {total_success} | Total Skipped: {total_skipped} | Total Errors: {total_errors}")
    print("=" * 60)

if __name__ == "__main__":
    main()
