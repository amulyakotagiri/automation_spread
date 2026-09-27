import gspread
from google.oauth2.service_account import Credentials
from pathlib import Path
import pandas as pd
import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]


def get_client():
    creds = Credentials.from_service_account_file(
        config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
    )
    return gspread.authorize(creds)


def get_or_create_spreadsheet(client, title: str, spreadsheet_id: str = ""):
    """
    Resolve a top-level spreadsheet without requiring a pre-created,
    manually-copied ID:
      1. If a spreadsheet_id is given (e.g. from a secret) and it's
         still accessible, use it.
      2. Otherwise search the service account's Drive for a spreadsheet
         with this exact title and use it if found.
      3. Otherwise create a new spreadsheet with this title.

    This means SHEET_ID_MIDCAP/RANGE/SMALLCAP become OPTIONAL — you can
    drop them from secrets entirely and the script will find or create
    the right spreadsheet by name every run.
    """
    if spreadsheet_id:
        try:
            return client.open_by_key(spreadsheet_id)
        except gspread.exceptions.APIError:
            print(f"  [SHEETS] Provided ID for '{title}' isn't accessible — "
                  f"falling back to search/create by name", flush=True)

    for f in client.list_spreadsheet_files():
        if f["name"] == title:
            print(f"  [SHEETS] Found existing spreadsheet: {title}", flush=True)
            return client.open_by_key(f["id"])

    print(f"  [SHEETS] Creating new spreadsheet: {title}", flush=True)
    sh = client.create(title)

    # Newly created spreadsheets are owned by the service account and
    # invisible in YOUR Drive/browser unless shared. If you set
    # SHARE_WITH_EMAIL (your Google account email) in config/secrets,
    # this shares it with you automatically as a writer.
    if getattr(config, "SHARE_WITH_EMAIL", ""):
        try:
            sh.share(config.SHARE_WITH_EMAIL, perm_type="user", role="writer")
            print(f"  [SHEETS] Shared '{title}' with {config.SHARE_WITH_EMAIL}", flush=True)
        except Exception as e:
            print(f"  [SHEETS] Could not auto-share '{title}': {e}", flush=True)
    else:
        print(f"  [SHEETS] NOTE: '{title}' was created by the service account and "
              f"won't show up in your own Drive unless you set SHARE_WITH_EMAIL "
              f"or manually share it: {sh.url}", flush=True)

    return sh


def get_or_create_stock_sheet(spreadsheet, symbol: str):
    """Get (or create) the worksheet for one stock within a spreadsheet."""
    try:
        return spreadsheet.worksheet(symbol)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=symbol, rows=500, cols=12)
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
        ws.update(header)
        ws.freeze(rows=9)
        return ws


def update_static_metrics(ws, security_id, volatility, atr, atr_pct, avg_vol):
    """Update the header section of a stock sheet"""
    from datetime import datetime
    import pytz
    now = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M")

    ws.update("B2", [[security_id]])
    ws.update("B3", [[now]])
    ws.update("B4", [[round(volatility, 2) if volatility else ""]])
    ws.update("B5", [[round(atr, 2) if atr else ""]])
    ws.update("B6", [[round(atr_pct, 2) if atr_pct else ""]])
    ws.update("B7", [[int(avg_vol) if avg_vol else ""]])


def append_live_row(ws, snapshot_time, session, bid, ask, spread, ltp, volume):
    """Append one live snapshot row"""
    row = [snapshot_time, session, bid, ask, spread, ltp, volume]
    ws.append_row(row, value_input_option="USER_ENTERED")
