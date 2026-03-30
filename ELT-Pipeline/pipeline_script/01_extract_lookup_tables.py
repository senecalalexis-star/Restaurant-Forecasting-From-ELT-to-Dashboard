# ============================================================
# SCRIPT 01 — EXTRACT LOOKUP TABLES
# item_id_table.csv + employee_table.csv
#
# NOTE: This script is shared for portfolio and documentation
# purposes. Paths and restaurant-specific identifiers have
# been anonymized. The script will not run as-is.
#
# EMPLOYEE TRACKING:
# Unique key = employee_id + server_name combo.
# If the same ID appears with a different name (e.g. a name
# change or staff reassignment), a new row is created.
# first_date_record and last_date_record track when each
# combo was first and last seen — useful for filtering server
# performance by season in downstream analysis.
# ============================================================
import pdfplumber
import pandas as pd
import unicodedata
import re
import sys
from pathlib import Path

# ============================================================
# CONFIGURATION
# Replace BASE_DIR with the actual path on your machine.
# RESTAURANT_HEADER must match the header line printed on
# every page of your POS transaction report.
# ============================================================
BASE_DIR          = Path(r"C:/path/to/your/pipeline/folder")
RESTAURANT_HEADER = "RESTAURANT NAME"

INPUT_DIR     = BASE_DIR / "INPUT"
OUTPUT_DIR    = BASE_DIR / "CSV"
CONVERTED_DIR = BASE_DIR / "CONVERT"

OUTPUT_ITEMS     = OUTPUT_DIR / "item_id_table.csv"
OUTPUT_EMPLOYEES = OUTPUT_DIR / "employee_table.csv"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CONVERTED_DIR.mkdir(parents=True, exist_ok=True)

# --- Helpers ---
def normalize(text):
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()

def clean_item_name(raw):
    if "/" in raw:
        raw = raw.split("/", 1)[1]
    return normalize(raw)

def parse_server_line(raw):
    m = re.match(r"^(\d+)\.(.+)$", raw.strip())
    if m:
        return int(m.group(1)), normalize(m.group(2))
    return None, None

def parse_date_str(raw):
    """Parse DD/MM/YY from PDF into YYYY-MM-DD string."""
    parts = raw.strip().split("/")
    if len(parts) == 3:
        d, m, y = parts
        return f"20{y}-{m.zfill(2)}-{d.zfill(2)}"
    return None

# --- Regex patterns ---
PAGE_HEADER_RE  = re.compile(r"^\d{1,2}/\d{2}/\d{2}\s+\d{2}:\d{2}\s+PAGE\s+\d+")
SERVER_LINE_RE  = re.compile(r"^(\d+)\.[A-Z][A-Z]+(?:\s|$)(?!.*\.)")
TABLE_HEADER_RE = re.compile(r"Table#\d+")
DATETIME_RE     = re.compile(r"^(\d{1,2}/\d{2}/\d{2})\s+\d{1,2}:\d{2}$")
ITEM_LINE_RE    = re.compile(r"^(\d+)\s+(.+?)\s+\$(\d+\.\d{2})\s+FP")

# ============================================================
# 1. LOAD EXISTING CSVs INTO MEMORY
# ============================================================

# --- Items ---
if OUTPUT_ITEMS.exists():
    df_items     = pd.read_csv(OUTPUT_ITEMS)
    items_set    = set(normalize(n) for n in df_items["item_name"].tolist())
    next_item_id = int(df_items["item_id"].max()) + 1
    print(f"✅ Loaded {len(df_items)} existing items.")
else:
    df_items     = pd.DataFrame(columns=["item_id", "item_name", "item_price"])
    items_set    = set()
    next_item_id = 1
    print("🆕 No item CSV found — starting fresh.")

# --- Employees ---
# Key: (employee_id, server_name) → {"first_date_record": str, "last_date_record": str}
# This allows multiple rows per employee_id if the name changes over time.
if OUTPUT_EMPLOYEES.exists():
    df_employees = pd.read_csv(OUTPUT_EMPLOYEES)
    # Ensure date columns exist (backwards compatibility)
    if "first_date_record" not in df_employees.columns:
        df_employees["first_date_record"] = None
    if "last_date_record" not in df_employees.columns:
        df_employees["last_date_record"] = None
    # Build lookup dict keyed by (employee_id, server_name)
    emp_combo_lookup = {
        (int(row["employee_id"]), str(row["server_name"])): {
            "first_date_record": row["first_date_record"],
            "last_date_record" : row["last_date_record"]
        }
        for _, row in df_employees.iterrows()
    }
    print(f"✅ Loaded {len(df_employees)} existing employee records.")
else:
    df_employees     = pd.DataFrame(columns=["employee_id", "server_name", "first_date_record", "last_date_record"])
    emp_combo_lookup = {}
    print("🆕 No employee CSV found — starting fresh.")

# ============================================================
# 2. FIND ALL PDFs (EARLY EXIT IF EMPTY)
# ============================================================
pdf_files = sorted(INPUT_DIR.glob("*.pdf"))
if not pdf_files:
    print(f"⚠️  No PDF files found in: {INPUT_DIR}")
    sys.exit(0)

print(f"\n📄 Found {len(pdf_files)} PDF file(s):")
for p in pdf_files:
    print(f"   • {p.name}")

# ============================================================
# 3. EXTRACTION LOGIC
# ============================================================
new_item_rows  = []
items_skipped  = 0

# Tracks updates to last_date_record for existing combos
# Key: (employee_id, server_name) → latest date string seen this run
emp_date_updates = {}

# Tracks new combos found this run
new_emp_combos = {}

for pdf_path in pdf_files:
    print(f"\n🔍 Processing: {pdf_path.name}")
    item_lines_found   = 0
    server_lines_found = 0
    in_receipt         = False
    pending_emp_id     = None
    pending_name       = None
    pending_date       = None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue
                for line in text.splitlines():
                    line = line.strip()
                    if PAGE_HEADER_RE.match(line): continue
                    if RESTAURANT_HEADER in line: continue

                    # --- Server line ---
                    if SERVER_LINE_RE.match(line) and not TABLE_HEADER_RE.search(line):
                        emp_id, server_name = parse_server_line(line)
                        if emp_id is not None:
                            pending_emp_id = emp_id
                            pending_name   = server_name
                            pending_date   = None
                        continue

                    # --- Date/time line — confirms server + captures date ---
                    m_dt = DATETIME_RE.match(line)
                    if m_dt and pending_emp_id is not None:
                        date_str = parse_date_str(m_dt.group(1))
                        pending_date = date_str
                        server_lines_found += 1
                        combo = (pending_emp_id, pending_name)

                        if combo in emp_combo_lookup:
                            # Combo exists — update last_date_record if newer
                            current_last = emp_combo_lookup[combo]["last_date_record"]
                            if date_str and (current_last is None or date_str > str(current_last)):
                                emp_combo_lookup[combo]["last_date_record"] = date_str
                                emp_date_updates[combo] = date_str
                        else:
                            # New combo — track it
                            if combo not in new_emp_combos:
                                new_emp_combos[combo] = {
                                    "first_date_record": date_str,
                                    "last_date_record" : date_str
                                }
                            else:
                                # Seen again in same run — update last date if newer
                                if date_str and date_str > new_emp_combos[combo]["last_date_record"]:
                                    new_emp_combos[combo]["last_date_record"] = date_str

                        pending_emp_id = None
                        pending_name   = None
                        pending_date   = None
                        continue

                    if pending_emp_id is not None and not DATETIME_RE.match(line):
                        pending_emp_id = None
                        pending_name   = None
                        pending_date   = None

                    # --- Item lines ---
                    if TABLE_HEADER_RE.search(line):
                        in_receipt = True
                        continue
                    if "===" in line:
                        in_receipt = False
                        continue

                    if in_receipt:
                        m = ITEM_LINE_RE.match(line)
                        if m:
                            item_lines_found += 1
                            qty        = int(m.group(1))
                            raw_name   = m.group(2).strip()
                            raw_price  = float(m.group(3))
                            unit_price = round(raw_price / qty, 2)
                            item_name  = clean_item_name(raw_name)
                            norm_name  = normalize(item_name)
                            if norm_name in items_set:
                                items_skipped += 1
                                continue
                            items_set.add(norm_name)
                            new_item_rows.append({
                                "item_id"   : next_item_id,
                                "item_name" : item_name,
                                "item_price": unit_price
                            })
                            next_item_id += 1

        print(f"   → {item_lines_found} item lines | {server_lines_found} server entries")

    except Exception as e:
        print(f"   ❌ Error processing {pdf_path.name}: {e}")

# ============================================================
# 4. SAVE CSVs
# ============================================================

# --- Items ---
if new_item_rows:
    new_df   = pd.DataFrame(new_item_rows)
    df_items = pd.concat([df_items.dropna(axis=1, how="all"), new_df], ignore_index=True)
    df_items.to_csv(OUTPUT_ITEMS, index=False, encoding="utf-8")
    print(f"\n✅ Items — {len(new_item_rows)} new | {items_skipped} skipped | {len(df_items)} total")
else:
    print(f"\n✅ Items — No new items. ({len(df_items)} total)")

# --- Employees ---
# Apply last_date_record updates to existing rows
if emp_date_updates:
    for idx, row in df_employees.iterrows():
        combo = (int(row["employee_id"]), str(row["server_name"]))
        if combo in emp_date_updates:
            df_employees.at[idx, "last_date_record"] = emp_date_updates[combo]

# Append new combos
if new_emp_combos:
    new_emp_rows = [
        {
            "employee_id"      : combo[0],
            "server_name"      : combo[1],
            "first_date_record": dates["first_date_record"],
            "last_date_record" : dates["last_date_record"]
        }
        for combo, dates in new_emp_combos.items()
    ]
    new_df       = pd.DataFrame(new_emp_rows)
    df_employees = pd.concat([df_employees.dropna(axis=1, how="all"), new_df], ignore_index=True)

if emp_date_updates or new_emp_combos:
    df_employees = df_employees.sort_values(["employee_id", "first_date_record"]).reset_index(drop=True)
    df_employees.to_csv(OUTPUT_EMPLOYEES, index=False, encoding="utf-8")
    print(f"✅ Employees — {len(new_emp_combos)} new combos | {len(emp_date_updates)} updated | {len(df_employees)} total rows")
else:
    print(f"✅ Employees — No changes. ({len(df_employees)} total rows)")
