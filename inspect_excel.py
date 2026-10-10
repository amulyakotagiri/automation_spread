from pathlib import Path
import pandas as pd
import config

path = Path(config.SYMBOLS_FILE)
print(f"Excel file: {path.resolve()}")
print(f"Exists: {path.exists()}\n")

xls = pd.ExcelFile(path)
print("=== Sheet names ===")
for i, sheet in enumerate(xls.sheet_names, 1):
    print(f"{i}. {sheet}")

print("\n=== Headers of each sheet ===")
for sheet in xls.sheet_names:
    df = pd.read_excel(xls, sheet_name=sheet, header=None)  # read raw
    print(f"\n--- Sheet: '{sheet}' ---")
    print(f"Shape: {df.shape}")
    print("First 5 rows:")
    print(df.head(5).to_string())
    print("-" * 60)
