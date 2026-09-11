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

def get_or_create_stock_sheet(client, spreadsheet_id: str, symbol: str):
    sh = client.open_by_key(spreadsheet_id)
    try:
        return sh.worksheet(symbol)
    except gspread.WorksheetNotFound:
        # Create new sheet for this stock
        ws = sh.add_worksheet(title=symbol, rows=500, cols=12)
        # Write static header template
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