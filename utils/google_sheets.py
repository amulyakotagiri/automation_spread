import time
from datetime import datetime
from pathlib import Path
import pytz
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import config
from utils.metrics import safe_val

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_client():
    creds_path = Path(config.GOOGLE_CREDENTIALS_FILE)
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Google credentials file not found at '{creds_path}'. "
            "Please ensure GOOGLE_SERVICE_ACCOUNT_JSON is configured."
        )
    creds = Credentials.from_service_account_file(
        str(creds_path), scopes=SCOPES
    )
    return gspread.authorize(creds)

def with_retry(func, max_retries=4, initial_wait=25):
    """Execute a function with exponential backoff on Google API rate limits (429)."""
    wait = initial_wait
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except gspread.exceptions.APIError as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "Quota exceeded" in err_msg:
                if attempt == max_retries:
                    raise
                print(f"    [RateLimit 429] Waiting {wait}s for Google API quota to reset ({attempt}/{max_retries})...")
                time.sleep(wait)
                wait = min(wait + 15, 60)
            else:
                raise
        except Exception:
            raise

def get_or_create_stock_sheet(client, spreadsheet_id: str, symbol: str):
    """
    Get or create a stock worksheet.
    For high volume loops, prefer caching `sh` and `worksheet_map`.
    """
    sh = client.open_by_key(spreadsheet_id)
    try:
        return sh.worksheet(symbol)
    except gspread.WorksheetNotFound:
        def _create():
            try:
                ws = sh.add_worksheet(title=symbol, rows=500, cols=12)
            except gspread.exceptions.APIError as e:
                if "already exists" in str(e):
                    return sh.worksheet(symbol)
                raise
            header = [
                ["SYMBOL", symbol],
                ["Security_ID", ""],
                ["Last_History_Update", ""],
                ["Volatility_6M_%", ""],
                ["ATR_14", ""],
                ["ATR_%_of_Price", ""],
                ["Avg_Volume_6M", ""],
                [],
                ["Snapshot_Time", "Session", "Bid", "Ask", "Spread (Bid-Ask)", "LTP", "Volume"]
            ]
            ws.update(values=header, range_name="A1:G9")
            ws.freeze(rows=9)
            return ws
        return with_retry(_create)

def update_static_metrics(ws, security_id, volatility, atr, atr_pct, avg_vol):
    """Update header section metrics in a single batch write (B2:B7)."""
    now = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M")
    metrics = [
        [str(security_id)],
        [now],
        [safe_val(volatility, 2)],
        [safe_val(atr, 2)],
        [safe_val(atr_pct, 2)],
        [safe_val(avg_vol, is_int=True)]
    ]
    with_retry(lambda: ws.update(values=metrics, range_name="B2:B7"))

def save_stock_historical(sh, worksheet_map: dict, symbol: str, security_id, volatility, atr, atr_pct, avg_vol) -> bool:
    """
    Optimized stock historical save:
    - If worksheet exists: updates B2:B7 in a single write call.
    - If worksheet is new: creates worksheet and writes header + metrics in a single call.
    Returns True if a new sheet was created, False if existing sheet was updated.
    """
    now = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M")
    vol_val = safe_val(volatility, 2)
    atr_val = safe_val(atr, 2)
    atrp_val = safe_val(atr_pct, 2)
    avgv_val = safe_val(avg_vol, is_int=True)

    ws = worksheet_map.get(symbol)
    if ws is None:
        # Check if the worksheet actually exists on the spreadsheet
        try:
            ws = sh.worksheet(symbol)
            worksheet_map[symbol] = ws
        except (gspread.WorksheetNotFound, gspread.exceptions.APIError):
            ws = None

    if ws is None:
        def _create_new():
            try:
                new_ws = sh.add_worksheet(title=symbol, rows=500, cols=12)
            except gspread.exceptions.APIError as e:
                if "already exists" in str(e):
                    found_ws = sh.worksheet(symbol)
                    worksheet_map[symbol] = found_ws
                    return found_ws, False
                raise

            header = [
                ["SYMBOL", symbol],
                ["Security_ID", str(security_id)],
                ["Last_History_Update", now],
                ["Volatility_6M_%", vol_val],
                ["ATR_14", atr_val],
                ["ATR_%_of_Price", atrp_val],
                ["Avg_Volume_6M", avgv_val],
                [],
                ["Snapshot_Time", "Session", "Bid", "Ask", "Spread (Bid-Ask)", "LTP", "Volume"]
            ]
            new_ws.update(values=header, range_name="A1:G9")
            new_ws.freeze(rows=9)
            worksheet_map[symbol] = new_ws
            return new_ws, True

        ws, is_new = with_retry(_create_new)
        if not is_new:
            metrics = [
                [str(security_id)],
                [now],
                [vol_val],
                [atr_val],
                [atrp_val],
                [avgv_val]
            ]
            with_retry(lambda: ws.update(values=metrics, range_name="B2:B7"))
        return is_new
    else:
        metrics = [
            [str(security_id)],
            [now],
            [vol_val],
            [atr_val],
            [atrp_val],
            [avgv_val]
        ]
        with_retry(lambda: ws.update(values=metrics, range_name="B2:B7"))
        return False

def append_live_row(ws, snapshot_time, session, bid, ask, spread, ltp, volume):
    """Append one live snapshot row."""
    row = [
        str(snapshot_time),
        str(session),
        safe_val(bid, 2),
        safe_val(ask, 2),
        safe_val(spread, 4),
        safe_val(ltp, 2),
        safe_val(volume, is_int=True)
    ]
    with_retry(lambda: ws.append_row(row, value_input_option="USER_ENTERED"))

def write_or_append(df: pd.DataFrame, worksheet_name: str, clear: bool = False, spreadsheet_id: str = None):
    """Helper for writing DataFrame to a sheet (used by main.py)."""
    client = get_client()
    sheet_id = spreadsheet_id or config.SPREADSHEET_IDS.get("MidCap")
    if not sheet_id:
        raise ValueError("No valid spreadsheet ID found for write_or_append")
    sh = client.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_name, rows=max(1000, len(df) + 10), cols=max(20, len(df.columns) + 2))

    # Replace NaN with empty strings
    clean_df = df.fillna("")
    values = [clean_df.columns.tolist()] + clean_df.values.tolist()

    if clear:
        ws.clear()
        ws.update(values=values, range_name="A1")
    else:
        # Append without header if data exists
        existing = ws.get_all_values()
        if not existing:
            ws.update(values=values, range_name="A1")
        else:
            ws.append_rows(clean_df.values.tolist(), value_input_option="USER_ENTERED")