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
from utils.google_sheets import get_client, get_or_create_spreadsheet, get_or_create_stock_sheet, update_static_metrics


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


def fetch_history(dhan, security_id: str, retries: int = 2) -> pd.DataFrame:
    """
    dhan.historical_daily_data() returns the full API envelope —
    {"status": "success", "data": {"close": [...], "high": [...], ...}} —
    not the OHLCV arrays directly, so resp["data"] must be unwrapped
    before checking for "close".
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
                "high": data["high"],
                "low": data["low"],
                "close": data["close"],
                "volume": data.get("volume", [0] * len(data["close"]))
            })
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            print(f"    Error: {e}")
            return pd.DataFrame()

    return pd.DataFrame()


def main():
    print("Starting weekly historical update...")
    dhan = dhanhq(DhanContext(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN))
    symbol_map = get_symbol_map()
    categories = load_symbols_by_category()
    client = get_client()

    for category, symbols in categories.items():
        # No more skipping when a Spreadsheet ID isn't configured —
        # get_or_create_spreadsheet finds it by name, or creates it,
        # since you've already shared Drive access with the service account.
        spreadsheet = get_or_create_spreadsheet(
            client,
            title=config.SPREADSHEET_TITLES[category],
            spreadsheet_id=config.SPREADSHEET_IDS.get(category, "")
        )

        print(f"\n=== Processing {category} ({len(symbols)} stocks) ===")

        for i, sym in enumerate(symbols, 1):
            sid = symbol_map.get(sym)
            if not sid:
                print(f"  [{i}] {sym} -> no security_id")
                continue

            print(f"  [{i}/{len(symbols)}] {sym}")

            hist = fetch_history(dhan, sid)
            if hist.empty:
                time.sleep(0.25)
                continue

            volatility = compute_volatility(hist)
            atr = compute_atr(hist, config.ATR_PERIOD)
            avg_vol = hist["volume"].mean()
            last_close = hist["close"].iloc[-1]
            atr_pct = (atr / last_close * 100) if (atr and not np.isnan(atr) and last_close) else None

            try:
                ws = get_or_create_stock_sheet(spreadsheet, sym)
                update_static_metrics(ws, sid, volatility, atr, atr_pct, avg_vol)
            except Exception as e:
                print(f"    Sheet write error: {e}")

            time.sleep(0.25)

    print("\nHistorical update completed.")


if __name__ == "__main__":
    main()
