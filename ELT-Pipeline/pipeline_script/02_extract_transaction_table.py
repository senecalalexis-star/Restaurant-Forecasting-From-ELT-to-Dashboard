# ============================================================
# SCRIPT 02 — EXTRACT TRANSACTION TABLE
# transaction_table.csv
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

INPUT_DIR   = BASE_DIR / "INPUT"
OUTPUT_DIR  = BASE_DIR / "CSV"
OUTPUT_FILE = OUTPUT_DIR / "transaction_table.csv"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Helpers ---
def normalize(text):
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()

def parse_date(raw):
    parts = raw.strip().split("/")
    if len(parts) == 3:
        d, m, y = parts
        return f"20{y}-{m.zfill(2)}-{d.zfill(2)}"
    return None

def parse_time(raw):
    parts = raw.strip().split(":")
    if len(parts) == 2:
        return f"{parts[0].zfill(2)}:{parts[1]}:00"
    return None

# --- Regex patterns ---
PAGE_HEADER_RE = re.compile(r"^\d{1,2}/\d{2}/\d{2}\s+\d{2}:\d{2}\s+PAGE\s+\d+")
SERVER_LINE_RE = re.compile(r"^(\d+)\.[A-Z][A-Z]+(?:\s|$)(?!.*\.)")
DATETIME_RE    = re.compile(r"^(\d{1,2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2})$")
CAISSE_RE      = re.compile(r"^Caisse#(\d+)$")
TRANSACTION_RE = re.compile(r"^(\d+)\s+\(\d+\)(?:\s+-\s+Table#(\d+))?")

# ============================================================
# 1. LOAD EXISTING CSV INTO MEMORY
# ============================================================
if OUTPUT_FILE.exists():
    df_tx           = pd.read_csv(OUTPUT_FILE)
    existing_tx_ids = set(df_tx["transaction_id"].astype(str).tolist())
    print(f"✅ Loaded {len(df_tx)} existing transactions.")
else:
    df_tx           = pd.DataFrame(columns=["transaction_id", "table", "employee_id", "transaction_date", "transaction_time", "caisse"])
    existing_tx_ids = set()
    print("🆕 No transaction CSV found — starting fresh.")

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
new_rows   = []
skipped_tx = 0

for pdf_path in pdf_files:
    print(f"\n🔍 Processing: {pdf_path.name}")
    rows_this_pdf       = 0
    pending_employee_id = None
    pending_date        = None
    pending_time        = None
    pending_caisse      = None

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
                    m_server = SERVER_LINE_RE.match(line)
                    if m_server:
                        pending_employee_id = int(m_server.group(1))
                        pending_date        = None
                        pending_time        = None
                        pending_caisse      = None
                        continue

                    # --- Date/time line ---
                    m_dt = DATETIME_RE.match(line)
                    if m_dt and pending_employee_id is not None:
                        pending_date = parse_date(m_dt.group(1))
                        pending_time = parse_time(m_dt.group(2))
                        continue

                    # --- Caisse line ---
                    m_caisse = CAISSE_RE.match(line)
                    if m_caisse and pending_employee_id is not None and pending_date:
                        pending_caisse = int(m_caisse.group(1))
                        continue

                    # --- Transaction header ---
                    m_tx = TRANSACTION_RE.match(line)
                    if m_tx and pending_employee_id is not None and pending_date:
                        tx_id = int(m_tx.group(1))
                        table = int(m_tx.group(2)) if m_tx.group(2) else None

                        if str(tx_id) in existing_tx_ids:
                            skipped_tx += 1
                        else:
                            existing_tx_ids.add(str(tx_id))
                            new_rows.append({
                                "transaction_id"  : tx_id,
                                "table"           : table,
                                "employee_id"     : pending_employee_id,
                                "transaction_date": pending_date,
                                "transaction_time": pending_time,
                                "caisse"          : pending_caisse
                            })
                            rows_this_pdf += 1

                        # Reset pending for next transaction block
                        pending_employee_id = None
                        pending_date        = None
                        pending_time        = None
                        pending_caisse      = None
                        continue

        print(f"   → {rows_this_pdf} new transactions")

    except Exception as e:
        print(f"   ❌ Error processing {pdf_path.name}: {e}")

# ============================================================
# 4. SAVE CSV
# ============================================================
if new_rows:
    new_df = pd.DataFrame(new_rows)
    df_tx  = pd.concat([df_tx.dropna(axis=1, how="all"), new_df], ignore_index=True)
    df_tx  = df_tx.sort_values(["transaction_date", "transaction_time"]).reset_index(drop=True)
    df_tx.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
    print(f"\n✅ Done! {len(new_rows)} new rows | {skipped_tx} skipped | {len(df_tx)} total")
else:
    print(f"\n✅ No new rows. CSV unchanged ({len(df_tx)} total).")
