from datetime import datetime, timedelta
from pathlib import Path
import time
import pandas as pd
import numpy as np
import pytz
from fyers_apiv3 import fyersModel
import config
from utils.security_master import get_symbol_map
from utils.metrics import (
    compute_atr,
    compute_volatility,
)
from bigquery_helper import (
    write_snapshot_records,
    write_static_metrics,
)

# ============================================================
# SETTINGS
# ============================================================
QUOTE_BATCH_SIZE = 50
API_DELAY_SECONDS = 1.25
MAX_RETRIES = 2
# ============================================================
# INVALID SYMBOLS (Fyers returns "Invalid symbol provided")
# ============================================================
INVALID_FYERS_SYMBOLS = {
    "DIACABS",
    "HEG",
    "HFCL",
    "LOTUSDEV",
    "STLTECH",
}
# ============================================================
# LOAD SYMBOLS FROM EXCEL
# ============================================================
def load_symbols_by_category() -> dict[str, list[str]]:
    path = Path(config.SYMBOLS_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {path.resolve()}")

    print(f"Loading symbols from: {path}")
    xls = pd.ExcelFile(path)

    categories = {
        "MidCap": [],
        "Range_100_1000": [],
        "SmallCap": [],
    }

    for sheet in xls.sheet_names:
        lower = sheet.lower().strip()

        # Skip any Universe / Dhan related sheets
        if any(x in lower for x in ["universe", "dhan", "dhanhq"]):
            print(f"Skipping sheet (Universe/Dhan): {sheet}")
            continue

        if "midcap" in lower:
            category = "MidCap"
        elif "range" in lower:
            category = "Range_100_1000"
        elif "small" in lower:
            category = "SmallCap"
        else:
            print(f"Skipping unknown sheet: {sheet}")
            continue

        df = pd.read_excel(xls, sheet_name=sheet, header=1)

        symbol_column = None
        for col in df.columns:
            col_name = str(col).strip().upper()
            if col_name in ["SYMBOL", "SYMBOLS"]:
                symbol_column = col
                break

        if symbol_column is None:
            print(f"WARNING: No SYMBOL column found in sheet {sheet}")
            continue

        symbols = (
            df[symbol_column]
            .dropna()
            .astype(str)
            .str.strip()
            .str.upper()
            .tolist()
        )

        # Remove empty + known invalid Fyers symbols
        cleaned = [
            s for s in symbols
            if s and s != "NAN" and s not in INVALID_FYERS_SYMBOLS
        ]

        categories[category].extend(cleaned)

    for category in categories:
        categories[category] = sorted(set(categories[category]))
        print(f"Loaded {len(categories[category])} unique symbols for {category}")

    return categories

# ============================================================
# FYERS SYMBOL CONVERSION
# ============================================================
def to_fyers_symbol(symbol: str) -> str:
    symbol = str(symbol).strip().upper()
    return f"NSE:{symbol}-EQ"

# ============================================================
# FYERS API ERROR DISPLAY
# ============================================================
def print_fyers_error(response, operation: str, symbol: str = ""):
    print("\n" + "=" * 70)
    print(f"FYERS {operation} ERROR")
    if symbol:
        print(f"Symbol: {symbol}")
    print(f"Response: {response}")
    if isinstance(response, dict):
        print(f"Status:  {response.get('s')}")
        print(f"Code:    {response.get('code')}")
        print(f"Message: {response.get('message')}")
    print("=" * 70 + "\n")

# ============================================================
# TEST FYERS CONNECTION  ← UPDATED WITH get_profile()
# ============================================================
def test_fyers_connection(fyers):
    """
    Test Profile + Quotes + History using RELIANCE.
    We stop the program if authentication is clearly broken.
    """
    test_symbol = "NSE:RELIANCE-EQ"

    print("\n")
    print("=" * 70)
    print("FYERS CONNECTION TEST")
    print("=" * 70)

    # --------------------------------------------------------
    # 1. PROFILE TEST (most important)
    # --------------------------------------------------------
    print("\n1. Testing get_profile() ...")
    try:
        profile_response = fyers.get_profile()
        print("Profile response:")
        print(profile_response)

        if profile_response.get("s") == "ok":
            print("✓ FYERS Profile API is working (token is valid)")
        else:
            print_fyers_error(profile_response, "PROFILE")
            print("\nSTOPPING HERE.")
            print("Access token is invalid / expired / wrong App.")
            print("Generate a fresh access token first.")
            return False
    except Exception as e:
        print(f"Profile API exception: {type(e).__name__}: {e}")
        return False

    # --------------------------------------------------------
    # 2. QUOTES TEST
    # --------------------------------------------------------
    print(f"\n2. Testing Quotes API with {test_symbol}")
    quote_request = {"symbols": test_symbol}
    try:
        quote_response = fyers.quotes(data=quote_request)
        print("Quotes response:")
        print(quote_response)

        if quote_response.get("s") == "ok":
            print("✓ FYERS Quotes API is working")
        else:
            print_fyers_error(quote_response, "QUOTES", test_symbol)
            print("WARNING: Quotes API failed.")
    except Exception as e:
        print(f"Quotes API exception: {type(e).__name__}: {e}")

    # --------------------------------------------------------
    # 3. HISTORY TEST
    # --------------------------------------------------------
    print(f"\n3. Testing History API with {test_symbol}")
    today = datetime.now().date()
    from_date = today - timedelta(days=10)

    range_from = int(datetime.combine(from_date, datetime.min.time()).timestamp())
    range_to = int(datetime.combine(today, datetime.max.time()).timestamp())

    history_request = {
        "symbol": test_symbol,
        "resolution": "D",
        "date_format": "0",
        "range_from": str(range_from),
        "range_to": str(range_to),
        "cont_flag": "1",
    }

    print("History request:")
    print(history_request)

    try:
        history_response = fyers.history(data=history_request)
        print("History response:")
        print(history_response)

        if history_response.get("s") == "ok":
            candles = history_response.get("candles", [])
            print(f"✓ FYERS History API is working ({len(candles)} candles)")
        else:
            print_fyers_error(history_response, "HISTORY", test_symbol)
            print("\nSTOPPING HERE.")
            print("The FYERS History API is not working.")
            return False
    except Exception as e:
        print(f"History API exception: {type(e).__name__}: {e}")
        return False

    print("\n" + "=" * 70)
    print("FYERS CONNECTION TEST PASSED")
    print("=" * 70)
    return True

# ============================================================
# FETCH QUOTES
# ============================================================
def fetch_quotes_batch(fyers, symbols: list[str]) -> dict:
    quote_cache = {}
    total = len(symbols)

    print("\n")
    print("=" * 70)
    print("FETCHING LIVE FYERS QUOTES")
    print("=" * 70)

    batches = [
        symbols[i:i + QUOTE_BATCH_SIZE]
        for i in range(0, total, QUOTE_BATCH_SIZE)
    ]

    print(f"Total symbols: {total}")
    print(f"Quote batches: {len(batches)}")

    for batch_number, batch in enumerate(batches, start=1):
        fyers_symbols = [to_fyers_symbol(symbol) for symbol in batch]
        request_data = {"symbols": ",".join(fyers_symbols)}

        success = False
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = fyers.quotes(data=request_data)
                if response.get("s") == "ok":
                    data = response.get("d", [])
                    for item in data:
                        fyers_name = item.get("n", "")
                        values = item.get("v", {})
                        if not fyers_name:
                            continue
                        original_symbol = (
                            fyers_name.replace("NSE:", "").replace("-EQ", "")
                        )
                        quote_cache[original_symbol] = values
                    print(f"[Quotes {batch_number}/{len(batches)}] Received {len(data)} quotes")
                    success = True
                    break
                else:
                    print_fyers_error(response, "QUOTES BATCH")
                    if attempt < MAX_RETRIES:
                        time.sleep(API_DELAY_SECONDS)
            except Exception as e:
                print(f"Quote batch exception: {type(e).__name__}: {e}")
                if attempt < MAX_RETRIES:
                    time.sleep(API_DELAY_SECONDS)

        if not success:
            print(f"WARNING: Quote batch {batch_number} failed.")

        if batch_number < len(batches):
            time.sleep(API_DELAY_SECONDS)

    print(f"\nTotal quotes received: {len(quote_cache)}")
    return quote_cache

# ============================================================
# FETCH DAILY HISTORY
# ============================================================
def fetch_history(fyers, symbol: str, retries: int = MAX_RETRIES) -> pd.DataFrame:
    today = datetime.now().date()
    from_date = today - timedelta(days=config.HISTORY_DAYS)

    range_from = int(datetime.combine(from_date, datetime.min.time()).timestamp())
    range_to = int(datetime.combine(today, datetime.max.time()).timestamp())

    fyers_symbol = to_fyers_symbol(symbol)

    request_data = {
        "symbol": fyers_symbol,
        "resolution": "D",
        "date_format": "0",
        "range_from": str(range_from),
        "range_to": str(range_to),
        "cont_flag": "1",
    }

    for attempt in range(retries + 1):
        try:
            response = fyers.history(data=request_data)
            if response.get("s") != "ok":
                if attempt == retries:
                    print_fyers_error(response, "HISTORY", symbol)
                if attempt < retries:
                    time.sleep(API_DELAY_SECONDS)
                    continue
                return pd.DataFrame()

            candles = response.get("candles", [])
            if not candles:
                return pd.DataFrame()

            df = pd.DataFrame(
                candles,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )

            for column in ["open", "high", "low", "close", "volume"]:
                df[column] = pd.to_numeric(df[column], errors="coerce")

            df = df.dropna(subset=["open", "high", "low", "close"])
            return df[["open", "high", "low", "close", "volume"]]

        except Exception as e:
            if attempt == retries:
                print(f"History exception {symbol}: {type(e).__name__}: {e}")
                return pd.DataFrame()
            time.sleep(API_DELAY_SECONDS)

    return pd.DataFrame()

# ============================================================
# EXTRACT QUOTE VALUES
# ============================================================
def extract_quote_values(quote: dict):
    if not quote:
        return None, None, None, None

    ltp = quote.get("lp") or quote.get("ltp")
    volume = quote.get("volume") or quote.get("v")
    bid = quote.get("bid") or quote.get("bid_price")
    ask = quote.get("ask") or quote.get("ask_price")

    return ltp, volume, bid, ask

# ============================================================
# MAIN
# ============================================================
def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Starting full automation with Fyers...")

    # ========================================================
    # CONFIGURATION CHECK
    # ========================================================
    print("\nConfiguration check:")
    print("FYERS_APP_ID present:", bool(config.FYERS_APP_ID))
    print("FYERS_ACCESS_TOKEN present:", bool(config.FYERS_ACCESS_TOKEN))
    print(
        "Token length:",
        len(config.FYERS_ACCESS_TOKEN) if config.FYERS_ACCESS_TOKEN else 0
    )

    if not config.FYERS_APP_ID:
        raise RuntimeError("FYERS_APP_ID is missing.")
    if not config.FYERS_ACCESS_TOKEN:
        raise RuntimeError("FYERS_ACCESS_TOKEN is missing.")

    # ========================================================
    # INITIALIZE FYERS
    # ========================================================
    fyers = fyersModel.FyersModel(
        client_id=config.FYERS_APP_ID,
        token=config.FYERS_ACCESS_TOKEN,
        is_async=False,
        log_path=""
    )

    # ========================================================
    # TEST FYERS BEFORE PROCESSING 533 STOCKS
    # ========================================================
    if not test_fyers_connection(fyers):
        raise RuntimeError(
            "\nFYERS connection test failed.\n"
            "Fix the token / App permissions / redirect URL first.\n"
            "The workflow was intentionally stopped."
        )

    # ========================================================
    # LOAD SECURITY MASTER
    # ========================================================
    print("\nDownloading Dhan Security Master...")
    symbol_map = get_symbol_map()
    print(f"Security master symbols: {len(symbol_map)}")

    # ========================================================
    # LOAD EXCEL SYMBOLS
    # ========================================================
    categories = load_symbols_by_category()
    valid = []
    for category, symbols in categories.items():
        for symbol in symbols:
            valid.append((symbol, category))

    print(f"\nProcessing {len(valid)} stocks...")

    # ========================================================
    # MARKET SESSION
    # ========================================================
    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)
    if now_ist.hour < 10:
        session = "Morning"
    elif now_ist.hour < 13:
        session = "Midday"
    else:
        session = "Closing"

    # ========================================================
    # FETCH LIVE QUOTES
    # ========================================================
    all_symbols = [symbol for symbol, category in valid]
    quote_cache = fetch_quotes_batch(fyers, all_symbols)

    # ========================================================
    # PROCESS STOCKS
    # ========================================================
    snapshot_records = []
    static_records = []
    fetch_failures = 0
    processed = 0

    print("\n")
    print("=" * 70)
    print("PROCESSING STOCK HISTORY")
    print("=" * 70)

    for idx, (symbol, category) in enumerate(valid, start=1):
        quote = quote_cache.get(symbol, {})
        ltp, volume, bid, ask = extract_quote_values(quote)

        # Spread
        spread = None
        if bid is not None and ask is not None:
            try:
                spread = round(float(ask) - float(bid), 4)
            except Exception:
                spread = None

        # History
        volatility = None
        atr = None
        atr_pct = None
        avg_vol = None

        hist = fetch_history(fyers, symbol)

        if hist.empty:
            fetch_failures += 1
        else:
            try:
                volatility = compute_volatility(hist)
            except Exception as e:
                print(f"Volatility calculation failed for {symbol}: {e}")

            try:
                atr_value = compute_atr(hist, config.ATR_PERIOD)
                if not np.isnan(atr_value):
                    atr = float(atr_value)
            except Exception as e:
                print(f"ATR calculation failed for {symbol}: {e}")

            if atr is not None and ltp is not None:
                try:
                    ltp_float = float(ltp)
                    if ltp_float != 0:
                        atr_pct = atr / ltp_float * 100
                except Exception:
                    atr_pct = None

            try:
                avg_vol = float(hist["volume"].mean())
            except Exception:
                avg_vol = None

        security_id = symbol_map.get(symbol, "")

        snapshot_records.append({
            "Snapshot_Time": now_ist,
            "Session": session,
            "Category": category,
            "SYMBOL": symbol,
            "security_id": str(security_id),
            "Bid": bid,
            "Ask": ask,
            "Spread": spread,
            "LTP": ltp,
            "Volume": volume,
        })

        static_records.append({
            "SYMBOL": symbol,
            "security_id": str(security_id),
            "Category": category,
            "last_history_update": now_ist,
            "volatility_6m": (
                round(float(volatility), 2) if volatility is not None else None
            ),
            "atr_14": (
                round(float(atr), 2) if atr is not None else None
            ),
            "atr_pct_of_price": (
                round(float(atr_pct), 2) if atr_pct is not None else None
            ),
            "avg_volume_6m": (
                round(float(avg_vol), 0) if avg_vol is not None else None
            ),
            "updated_at": now_ist,
        })

        processed += 1

        if idx % 25 == 0 or idx == len(valid):
            print(
                f"[{idx}/{len(valid)}] {symbol} ({category}) | "
                f"Quotes: {len(quote_cache)} | "
                f"History failures: {fetch_failures}"
            )

    # ========================================================
    # WRITE TO BIGQUERY
    # ========================================================
    print("\n")
    print("=" * 70)
    print("WRITING TO BIGQUERY")
    print("=" * 70)
    print(f"Snapshot rows: {len(snapshot_records)}")
    print(f"Static metric rows: {len(static_records)}")

    write_snapshot_records(snapshot_records)
    write_static_metrics(static_records)

    # ========================================================
    # FINAL SUMMARY
    # ========================================================
    print("\n")
    print("=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"Stocks processed: {processed}/{len(valid)}")
    print(f"Live quotes received: {len(quote_cache)}/{len(valid)}")
    print(f"History failures: {fetch_failures}/{len(valid)}")
    print(f"BigQuery snapshot rows: {len(snapshot_records)}")
    print(f"BigQuery static metric rows: {len(static_records)}")
    print("\nDone.")

if __name__ == "__main__":
    main()
