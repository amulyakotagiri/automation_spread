import os
from dotenv import load_dotenv

load_dotenv()

# Dhan
DHAN_CLIENT_ID    = os.getenv("DHAN_CLIENT_ID")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN")

# Google
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials/service_account.json")

# Optional: your own Google account email. If set, any spreadsheet this
# script auto-creates gets shared with you as a writer so it shows up
# in your own Drive/browser (service-account-created files otherwise
# stay invisible to you).
SHARE_WITH_EMAIL = os.getenv("SHARE_WITH_EMAIL", "")

# Optional: the ID of a Drive folder you've already shared with the
# service account (Editor access). If set, newly auto-created
# spreadsheets are created directly inside it and inherit the folder's
# sharing — no per-file share call needed. Takes priority over
# SHARE_WITH_EMAIL when both are set. Find the folder ID in its URL:
# https://drive.google.com/drive/folders/<THIS_PART>
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID", "")

# Spreadsheet IDs — now OPTIONAL. If left blank (or the ID becomes
# inaccessible), get_or_create_spreadsheet() will find-or-create a
# spreadsheet by the matching title below instead. Keep these secrets
# set if you want a pinned, known ID; leave them blank for full
# automation.
SPREADSHEET_IDS = {
    "MidCap": os.getenv("SHEET_ID_MIDCAP", ""),
    "Range_100_1000": os.getenv("SHEET_ID_RANGE", ""),
    "SmallCap": os.getenv("SHEET_ID_SMALLCAP", "")
}

# Titles used to find-or-create each category's spreadsheet by name.
SPREADSHEET_TITLES = {
    "MidCap": "NSE MidCap Screener",
    "Range_100_1000": "NSE 100-1000 Range Screener",
    "SmallCap": "NSE SmallCap Screener"
}

# Data
SYMBOLS_FILE = "data/symbols.xlsx"
HISTORY_DAYS = 180
ATR_PERIOD = 14
BATCH_SIZE = 70
BATCH_SIZE_QUOTES = 70

# Airtable (used by other scripts in this repo, not main_live.py)
AIRTABLE_TOKEN   = os.getenv("AIRTABLE_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
