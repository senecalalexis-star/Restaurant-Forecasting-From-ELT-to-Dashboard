# ============================================================
# SCRIPT 01 — EXTRACT LOOKUP TABLES
# item_id_table.csv + employee_table.csv
#
# NOTE: This script is shared for portfolio and documentation
# purposes. Paths and restaurant-specific identifiers have
# been anonymized. The script will not run as-is.
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

# --- Regex patterns ---
PAGE_HEADER_RE = re.compile(r"^\d{1,2}/\d{2}/\d{2}\s+\d{2}:\d{2}\s+PAGE\s+\d+")
SERVER_LINE_RE = re.compile(r"^(\d+)\.[A-Z][A-Z]+(?:\s|$)(?!.*\.)")
TABLE_HEADER_RE = re.compile(r"Table#\d+")
DATETIME_RE    = re.compile(r"^\d{1,2}/\d{2}/\d{2}\s+\d{1,2}:\d{2}$")
ITEM_LINE_RE   = re.compile(r"^(\d+)\s+(.+?)\s+\$(\d+\.\d{2})\s+FP")

# ============================================================
# 1. LOAD EXISTING CSVs INTO MEMORY
# ============================================================
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

if OUTPUT_EMPLOYEES.exists():
    df_employees       = pd.read_csv(OUTPUT_EMPLOYEES)
    employees_id_set   = set(df_employees["employee_id"].astype(int).tolist())
    employees_name_set = set(normalize(n) for n in df_employees["server_name"].tolist())
    print(f"✅ Loaded {len(df_employees)} existing employees.")
else:
    df_employees       = pd.DataFrame(columns=["employee_id", "server_name"])
    employees_id_set   = set()
    employees_name_set = set()
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
new_item_rows = []
new_emp_rows  = []
items_skipped = 0
emps_skipped  = 0

for pdf_path in pdf_files:
    print(f"\n🔍 Processing: {pdf_path.name}")
    item_lines_found   = 0
    server_lines_found = 0
    in_receipt         = False
    pending_emp_id     = None
    pending_name       = None

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

                    if SERVER_LINE_RE.match(line) and not TABLE_HEADER_RE.search(line):
                        emp_id, server_name = parse_server_line(line)
                        if emp_id is not None:
                            pending_emp_id = emp_id
                            pending_name   = server_name
                        continue

                    if DATETIME_RE.match(line) and pending_emp_id is not None:
                        server_lines_found += 1
                        if pending_emp_id not in employees_id_set:
                            employees_id_set.add(pending_emp_id)
                            employees_name_set.add(pending_name)
                            new_emp_rows.append({"employee_id": pending_emp_id, "server_name": pending_name})
                        else:
                            emps_skipped += 1
                        pending_emp_id = None
                        pending_name   = None
                        continue

                    if pending_emp_id is not None and not DATETIME_RE.match(line):
                        pending_emp_id = None
                        pending_name   = None

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
                            new_item_rows.append({"item_id": next_item_id, "item_name": item_name, "item_price": unit_price})
                            next_item_id += 1

        print(f"   → {item_lines_found} item lines | {server_lines_found} server lines")

    except Exception as e:
        print(f"   ❌ Error processing {pdf_path.name}: {e}")

# ============================================================
# 4. SAVE CSVs
# ============================================================
if new_item_rows:
    new_df   = pd.DataFrame(new_item_rows)
    df_items = pd.concat([df_items.dropna(axis=1, how="all"), new_df], ignore_index=True)
    df_items.to_csv(OUTPUT_ITEMS, index=False, encoding="utf-8")
    print(f"\n✅ Items — {len(new_item_rows)} new | {items_skipped} skipped | {len(df_items)} total")
else:
    print(f"\n✅ Items — No new items. ({len(df_items)} total)")

if new_emp_rows:
    new_df       = pd.DataFrame(new_emp_rows)
    df_employees = pd.concat([df_employees.dropna(axis=1, how="all"), new_df], ignore_index=True)
    df_employees = df_employees.sort_values("employee_id").reset_index(drop=True)
    df_employees.to_csv(OUTPUT_EMPLOYEES, index=False, encoding="utf-8")
    print(f"✅ Employees — {len(new_emp_rows)} new | {emps_skipped} skipped | {len(df_employees)} total")
else:
    print(f"✅ Employees — No new employees. ({len(df_employees)} total)")
