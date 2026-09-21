import os
from dotenv import load_dotenv

load_dotenv()

# Dhan
DHAN_CLIENT_ID    = os.getenv("DHAN_CLIENT_ID")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN")

# Google
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials/service_account.json")

# Spreadsheet IDs
SPREADSHEET_IDS = {
    "MidCap": os.getenv("SHEET_ID_MIDCAP", ""),
    "Range_100_1000": os.getenv("SHEET_ID_RANGE", ""),
    "SmallCap": os.getenv("SHEET_ID_SMALLCAP", "")
}

# Data
SYMBOLS_FILE = "data/symbols.xlsx"
HISTORY_DAYS = 180
ATR_PERIOD = 14
BATCH_SIZE = 70
BATCH_SIZE_QUOTES = 70   # <-- added (was missing)
