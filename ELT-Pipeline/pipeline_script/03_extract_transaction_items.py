# ============================================================
# SCRIPT 03 — EXTRACT TRANSACTION ITEMS TABLE
# transaction_items.csv
#
# NOTE: This script is shared for portfolio and documentation
# purposes. Paths and restaurant-specific identifiers have
# been anonymized. The script will not run as-is.
#
# DEPENDENCY: Requires item_id_table.csv built by script 01.
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

OUTPUT_FILE   = OUTPUT_DIR / "transaction_items.csv"
ITEM_ID_TABLE = OUTPUT_DIR / "item_id_table.csv"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Helpers ---
def normalize(text):
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()

def clean_item_name(raw):
    if "/" in raw:
        raw = raw.split("/", 1)[1]
    return normalize(raw)

# --- Regex patterns ---
PAGE_HEADER_RE = re.compile(r"^\d{1,2}/\d{2}/\d{2}\s+\d{2}:\d{2}\s+PAGE\s+\d+")
SERVER_LINE_RE = re.compile(r"^(\d+)\.[A-Z][A-Z]+(?:\s|$)(?!.*\.)")
DATETIME_RE    = re.compile(r"^(\d{1,2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2})$")
TRANSACTION_RE = re.compile(r"^(\d+)\s+\(\d+\)(?:\s+-\s+Table#(\d+))?")
ITEM_LINE_RE   = re.compile(r"^(\d+(?:\.\d+)?)\s+(.+?)\s+\x24(\d+\.\d{2})\s+FP$")

# ============================================================
# 1. LOAD ITEM_ID_TABLE (REQUIRED)
# ============================================================
if not ITEM_ID_TABLE.exists():
    print(f"❌ Error: {ITEM_ID_TABLE.name} not found! Run Script 01 first.")
    sys.exit(1)

df_items    = pd.read_csv(ITEM_ID_TABLE)
item_lookup = {normalize(row["item_name"]): int(row["item_id"])
               for _, row in df_items.iterrows()}
print(f"✅ Loaded {len(item_lookup)} items into lookup table.")

# ============================================================
# 2. LOAD EXISTING DATA
# ============================================================
if OUTPUT_FILE.exists():
    df_tx_items     = pd.read_csv(OUTPUT_FILE)
    existing_tx_ids = set(df_tx_items["transaction_id"].astype(str).tolist())
    print(f"✅ Loaded {len(df_tx_items)} existing rows | {len(existing_tx_ids)} unique transactions.")
else:
    df_tx_items     = pd.DataFrame(columns=["transaction_id", "item_id", "quantity"])
    existing_tx_ids = set()
    print("🆕 No transaction_items CSV found — starting fresh.")

# ============================================================
# 3. FIND ALL PDFs (EARLY EXIT IF EMPTY)
# ============================================================
pdf_files = sorted(INPUT_DIR.glob("*.pdf"))
if not pdf_files:
    print(f"⚠️  No PDF files found in: {INPUT_DIR}")
    sys.exit(0)

print(f"\n📄 Found {len(pdf_files)} PDF file(s):")
for p in pdf_files:
    print(f"   • {p.name}")

# ============================================================
# 4. EXTRACTION LOGIC
# ============================================================
new_rows   = []
skipped_tx = 0
not_found  = 0

for pdf_path in pdf_files:
    print(f"\n🔍 Processing: {pdf_path.name}")
    rows_this_pdf       = 0
    current_tx_id       = None
    pending_employee_id = None
    pending_date        = None
    in_receipt          = False

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

                    m_server = SERVER_LINE_RE.match(line)
                    if m_server:
                        pending_employee_id = int(m_server.group(1))
                        pending_date        = None
                        in_receipt          = False
                        continue

                    m_dt = DATETIME_RE.match(line)
                    if m_dt and pending_employee_id is not None:
                        pending_date = m_dt.group(1)
                        continue

                    m_tx = TRANSACTION_RE.match(line)
                    if m_tx and pending_employee_id is not None and pending_date:
                        current_tx_id       = int(m_tx.group(1))
                        in_receipt          = True
                        pending_employee_id = None
                        pending_date        = None
                        continue

                    if "===" in line:
                        in_receipt    = False
                        current_tx_id = None
                        continue

                    if in_receipt and current_tx_id is not None:
                        m = ITEM_LINE_RE.match(line)
                        if m:
                            if str(current_tx_id) in existing_tx_ids:
                                skipped_tx += 1
                                continue
                            qty       = float(m.group(1))
                            qty       = int(qty) if qty == int(qty) else qty
                            raw_name  = m.group(2).strip()
                            item_name = clean_item_name(raw_name)
                            item_id   = item_lookup.get(normalize(item_name))

                            if item_id is None:
                                not_found += 1
                                continue

                            new_rows.append({"transaction_id": current_tx_id, "item_id": item_id, "quantity": qty})
                            rows_this_pdf += 1

        print(f"   → {rows_this_pdf} item rows extracted")

    except Exception as e:
        print(f"   ❌ Error processing {pdf_path.name}: {e}")

# ============================================================
# 5. SAVE CSV
# ============================================================
if new_rows:
    new_df      = pd.DataFrame(new_rows)
    df_tx_items = pd.concat([df_tx_items.dropna(axis=1, how="all"), new_df], ignore_index=True)
    df_tx_items = df_tx_items.sort_values("transaction_id").reset_index(drop=True)
    df_tx_items.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
    print(f"\n✅ Done! {len(new_rows)} new rows | {skipped_tx} skipped | {not_found} not found | {len(df_tx_items)} total")
else:
    print(f"\n✅ No new rows. CSV unchanged ({len(df_tx_items)} total).")
