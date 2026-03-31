# ============================================================
# SCRIPT 09 — IMPORT CSVs TO SUPABASE
# Reads CSVs from the CSV folder and imports them to Supabase.
#
# NOTE: This script is shared for portfolio and documentation
# purposes. Paths and credentials have been anonymized.
# The script will not run as-is.
#
# Behaviour per table type:
#   REFERENCE tables   → append new rows only (no duplicates)
#                        tracked via import_state.json
#   TRANSACTION tables → append all rows, move CSV to history
#                        with date in filename after success
#   WEATHER HISTORICAL → append from last fetched date only
#                        tracked via weather_last_fetch.txt
#   WEATHER FORECAST   → append all rows, move CSV to history
#                        with date in filename after success
# ============================================================

import os
import json
import shutil
import time
import psycopg2
import psycopg2.extras
import pandas as pd
from pathlib import Path
from datetime import date, datetime
from dotenv import load_dotenv

# ============================================================
# CONFIGURATION
# Replace BASE_DIR with the actual path on your machine.
# Credentials are loaded from a .env file — never hardcoded.
# ============================================================
BASE_DIR     = Path(r"C:/path/to/your/pipeline/folder")
CSV_DIR      = BASE_DIR / "CSV"
HISTORY_DIR  = CSV_DIR / "CSV history"
SCRIPT_DIR   = BASE_DIR / "SCRIPTS"
STATE_FILE   = SCRIPT_DIR / "import_state.json"
WEATHER_FILE = SCRIPT_DIR / "weather_last_fetch.txt"

HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# LOAD ENVIRONMENT VARIABLES
# ============================================================
load_dotenv(SCRIPT_DIR / "credential.env")

DB_CONFIG = {
    "host"    : os.getenv("SUPABASE_HOST"),
    "dbname"  : os.getenv("SUPABASE_DB", "postgres"),
    "user"    : os.getenv("SUPABASE_USER", "postgres"),
    "password": os.getenv("SUPABASE_PASSWORD"),
    "port"    : os.getenv("SUPABASE_PORT", "5432"),
    "sslmode" : "require"
}

# ============================================================
# TABLE CONFIGURATION
# ============================================================

# Reference tables: append only new rows based on unique key column
# key_col = the business key to check for duplicates (NOT the id column)
REFERENCE_TABLES = {
    "category_table"        : {"schema": "raw_reference", "table": "category_reference",      "key_col": "category_id"},
    "discount_type_table"   : {"schema": "raw_reference", "table": "discount_type_reference", "key_col": "discount_id"},
    "employee_table"        : {"schema": "raw_reference", "table": "employee_reference",       "key_col": "employee_id"},
    "item_id_table"         : {"schema": "raw_reference", "table": "item_reference",           "key_col": "item_id"},
    "payment_type_table"    : {"schema": "raw_reference", "table": "payment_type_reference",   "key_col": "payment_id"},
    "event_reference"       : {"schema": "raw_reference", "table": "event_reference",          "key_col": "event_name"},
    "weather_code_reference": {"schema": "raw_weather",   "table": "weather_code_reference",   "key_col": "weather_code"},
}

# Transaction tables: append all rows, move CSV to history after success
# rename: optional dict to rename CSV columns to match database column names
TRANSACTION_TABLES = {
    "transaction_table"    : {"schema": "raw_transaction", "table": "transaction_table",   "rename": {"table": "table_number"}},
    "transaction_items"    : {"schema": "raw_transaction", "table": "transaction_item",    "rename": {}},
    "transaction_totals"   : {"schema": "raw_transaction", "table": "transaction_total",   "rename": {}},
    "transaction_payments" : {"schema": "raw_transaction", "table": "transaction_payment", "rename": {}},
    "transaction_discounts": {"schema": "raw_transaction", "table": "transaction_discount","rename": {}},
}

# ============================================================
# HELPERS
# ============================================================

def get_connection():
    """Open a psycopg2 connection to Supabase."""
    return psycopg2.connect(**DB_CONFIG)

def get_connection_with_retry(retries: int = 3, delay: int = 5):
    """Open a psycopg2 connection with retry logic for transient auth errors."""
    last_error = None
    for attempt in range(retries):
        try:
            return get_connection()
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                print(f"   ⚠️  Connection attempt {attempt + 1} failed, retrying in {delay}s...")
                time.sleep(delay)
    raise last_error

def load_state() -> dict:
    """Load import state from JSON file."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state: dict):
    """Save import state to JSON file."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def move_to_history(csv_path: Path):
    """Move a CSV file to history folder with today's date in the filename."""
    today    = date.today().strftime("%Y-%m-%d")
    new_name = f"{csv_path.stem}_{today}{csv_path.suffix}"
    dest     = HISTORY_DIR / new_name
    if dest.exists():
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        new_name  = f"{csv_path.stem}_{timestamp}{csv_path.suffix}"
        dest      = HISTORY_DIR / new_name
    shutil.move(str(csv_path), str(dest))
    print(f"   📦 Moved to history: {new_name}")

def build_insert_query(schema: str, table: str, columns: list) -> str:
    """Build a parameterized INSERT query.
    Quotes all column names to safely handle PostgreSQL reserved words like 'table'.
    """
    cols         = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    return f'INSERT INTO {schema}.{table} ({cols}) VALUES ({placeholders})'

def clean_row(row) -> tuple:
    """Convert NaN values to None for psycopg2 compatibility."""
    return tuple(None if pd.isna(v) else v for v in row)

# ============================================================
# IMPORT FUNCTIONS
# ============================================================

def import_reference_table(csv_name: str, config: dict, state: dict):
    """
    Import a reference table CSV.
    Only inserts rows whose key_col value is not already in the database.
    Updates import_state.json after success.
    """
    csv_path  = CSV_DIR / f"{csv_name}.csv"
    if not csv_path.exists():
        print(f"   ⚠️  {csv_name}.csv not found — skipping")
        return

    schema    = config["schema"]
    table     = config["table"]
    key_col   = config["key_col"]
    full_name = f"{schema}.{table}"

    print(f"\n   📥 Importing reference: {full_name}")

    df = pd.read_csv(csv_path)
    df = df.drop(columns=["id"], errors="ignore")

    if df.empty:
        print(f"   ⚠️  {csv_name}.csv is empty — skipping")
        return

    columns = list(df.columns)

    try:
        conn = get_connection_with_retry()
        cur  = conn.cursor()

        # INSERT ... ON CONFLICT DO NOTHING lets PostgreSQL handle
        # deduplication in one shot — safe to re-run at any time
        cols         = ", ".join(f'"{c}"' for c in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        query = (
            f'INSERT INTO {full_name} ({cols}) VALUES ({placeholders}) '
            f'ON CONFLICT ("{key_col}") DO NOTHING'
        )

        rows = [clean_row(row) for row in df.itertuples(index=False)]
        psycopg2.extras.execute_batch(cur, query, rows, page_size=500)
        conn.commit()

        print(f"   ✅ Processed {len(rows)} rows into {full_name}")
        state[csv_name] = {
            "last_import"  : date.today().strftime("%Y-%m-%d"),
            "rows_inserted": len(rows)
        }
        cur.close()
        conn.close()

    except Exception as e:
        print(f"   ❌ Error importing {full_name}: {e}")
        try: conn.rollback(); conn.close()
        except: pass


def import_transaction_table(csv_name: str, config: dict):
    """
    Import a transaction table CSV.
    Appends all rows then moves CSV to history with date in filename.
    """
    csv_path  = CSV_DIR / f"{csv_name}.csv"
    if not csv_path.exists():
        print(f"   ⚠️  {csv_name}.csv not found — skipping")
        return

    schema    = config["schema"]
    table     = config["table"]
    full_name = f"{schema}.{table}"

    print(f"\n   📥 Importing transactions: {full_name}")

    df = pd.read_csv(csv_path)
    df = df.drop(columns=["id"], errors="ignore")

    rename_map = config.get("rename", {})
    if rename_map:
        df = df.rename(columns=rename_map)

    if df.empty:
        print(f"   ⚠️  {csv_name}.csv is empty — skipping")
        return

    columns = list(df.columns)

    try:
        conn  = get_connection_with_retry()
        cur   = conn.cursor()
        query = build_insert_query(schema, table, columns)
        rows  = [clean_row(row) for row in df.itertuples(index=False)]
        psycopg2.extras.execute_batch(cur, query, rows, page_size=500)
        conn.commit()
        print(f"   ✅ Inserted {len(rows)} rows into {full_name}")
        cur.close()
        conn.close()
        move_to_history(csv_path)

    except Exception as e:
        print(f"   ❌ Error importing {full_name}: {e}")
        print(f"   ⚠️  CSV NOT moved — fix the error and re-run")
        try: conn.rollback(); conn.close()
        except: pass


def import_weather_historical():
    """
    Import historical weather CSV.
    Only appends rows from weather_last_fetch.txt date onwards.
    Updates weather_last_fetch.txt after success.
    CSV stays in place — never moved.
    """
    csv_path = CSV_DIR / "weather_historical.csv"
    if not csv_path.exists():
        print(f"   ⚠️  weather_historical.csv not found — skipping")
        return

    print(f"\n   📥 Importing weather: raw_weather.historical_weather")

    if WEATHER_FILE.exists():
        last_fetch = pd.to_datetime(WEATHER_FILE.read_text().strip()).date()
        print(f"   📅 Appending from {last_fetch} onwards")
    else:
        last_fetch = date(2020, 1, 1)
        print(f"   📅 No weather_last_fetch.txt found — importing from {last_fetch}")

    df = pd.read_csv(csv_path)
    df = df.drop(columns=["id"], errors="ignore")
    df["date"] = pd.to_datetime(df["date"]).dt.date
    new_rows_df = df[df["date"] > last_fetch]

    if new_rows_df.empty:
        print(f"   ✅ No new historical weather rows to insert")
        return

    columns = list(new_rows_df.columns)

    try:
        conn  = get_connection_with_retry()
        cur   = conn.cursor()
        query = build_insert_query("raw_weather", "historical_weather", columns)
        rows  = [clean_row(row) for row in new_rows_df.itertuples(index=False)]
        psycopg2.extras.execute_batch(cur, query, rows, page_size=500)
        conn.commit()
        print(f"   ✅ Inserted {len(rows)} new historical weather rows")
        WEATHER_FILE.write_text(date.today().strftime("%Y-%m-%d"))
        print(f"   📅 Updated weather_last_fetch.txt to {date.today()}")
        cur.close()
        conn.close()

    except Exception as e:
        print(f"   ❌ Error importing historical weather: {e}")
        try: conn.rollback(); conn.close()
        except: pass


def import_weather_forecast():
    """
    Import forecast weather CSV.
    Appends all rows, moves CSV to history after success.
    """
    csv_path = CSV_DIR / "weather_forecast.csv"
    if not csv_path.exists():
        print(f"   ⚠️  weather_forecast.csv not found — skipping")
        return

    print(f"\n   📥 Importing weather: raw_weather.forecast_weather")

    df = pd.read_csv(csv_path)
    df = df.drop(columns=["id"], errors="ignore")

    if df.empty:
        print(f"   ⚠️  weather_forecast.csv is empty — skipping")
        return

    columns = list(df.columns)

    try:
        conn  = get_connection_with_retry()
        cur   = conn.cursor()
        query = build_insert_query("raw_weather", "forecast_weather", columns)
        rows  = [clean_row(row) for row in df.itertuples(index=False)]
        psycopg2.extras.execute_batch(cur, query, rows, page_size=500)
        conn.commit()
        print(f"   ✅ Inserted {len(rows)} forecast weather rows")
        cur.close()
        conn.close()
        move_to_history(csv_path)

    except Exception as e:
        print(f"   ❌ Error importing forecast weather: {e}")
        print(f"   ⚠️  CSV NOT moved — fix the error and re-run")
        try: conn.rollback(); conn.close()
        except: pass


# ============================================================
# MAIN
# ============================================================

def run_import():
    print("=" * 60)
    print("  SCRIPT 09 — SUPABASE IMPORT")
    print("=" * 60)

    state = load_state()

    # --- Reference tables ---
    print("\n📚 PHASE 1 — Reference tables")
    for csv_name, config in REFERENCE_TABLES.items():
        import_reference_table(csv_name, config, state)

    save_state(state)

    # --- Transaction tables ---
    print("\n💳 PHASE 2 — Transaction tables")
    for csv_name, config in TRANSACTION_TABLES.items():
        import_transaction_table(csv_name, config)

    # --- Weather ---
    print("\n🌤️  PHASE 3 — Weather tables")
    import_weather_historical()
    import_weather_forecast()

    print(f"\n{'=' * 60}")
    print("  ✅ IMPORT COMPLETE")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    run_import()
