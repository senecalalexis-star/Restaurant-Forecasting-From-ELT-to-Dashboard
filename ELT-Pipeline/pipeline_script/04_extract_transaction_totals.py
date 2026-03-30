# ============================================================
# SCRIPT 04 — EXTRACT TRANSACTION TOTALS TABLE
# transaction_totals.csv
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
OUTPUT_FILE = OUTPUT_DIR / "transaction_totals.csv"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Helpers ---
def normalize(text):
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()

def parse_amount(text):
    m = re.search(r'\$(-?[0-9]+\.[0-9]{2})', text)
    return float(m.group(1)) if m else None

# --- Regex patterns ---
PAGE_HEADER_RE    = re.compile(r"^\d{1,2}/\d{2}/\d{2}\s+\d{2}:\d{2}\s+PAGE\s+\d+")
SERVER_LINE_RE    = re.compile(r"^(\d+)\.[A-Z][A-Z]+(?:\s|$)(?!.*\.)")
DATETIME_RE       = re.compile(r"^(\d{1,2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2})$")
TRANSACTION_RE    = re.compile(r"^(\d+)\s+\(\d+\)(?:\s+-\s+Table#(\d+))?")
SEPARATOR_RE      = re.compile(r"^={5,}")
SUBTOTAL_RE       = re.compile(r"^Sous-total\s+\$")
TPS_RE            = re.compile(r"^\d+\.T\.P\.S\s+\$")
TVQ_RE            = re.compile(r"^\d+\.T\.V\.Q\s+\$")
TOTAL_RE          = re.compile(r"^Total\s+\$")
ARRONDISSEMENT_RE = re.compile(r"^\d+\.ARRONDISSEMENT\s+\$")
TPS_NUMBER_RE     = re.compile(r"^T\.P\.S\s+\d")

# ============================================================
# 1. LOAD EXISTING CSV INTO MEMORY
# ============================================================
if OUTPUT_FILE.exists():
    df_totals       = pd.read_csv(OUTPUT_FILE)
    existing_tx_ids = set(df_totals["transaction_id"].astype(str).tolist())
    print(f"✅ Loaded {len(df_totals)} existing rows | {len(existing_tx_ids)} unique transactions.")
else:
    df_totals       = pd.DataFrame(columns=["transaction_id", "subtotal", "tps", "tvq", "total", "arrondissement"])
    existing_tx_ids = set()
    print("🆕 No transaction_totals CSV found — starting fresh.")

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
    current_tx_id       = None
    pending_employee_id = None
    pending_date        = None
    in_totals_block     = False
    in_payment_block    = False
    state = {"subtotal": None, "tps": None, "tvq": None, "total": None, "arrondissement": None}

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
                        in_totals_block     = False
                        in_payment_block    = False
                        continue

                    m_dt = DATETIME_RE.match(line)
                    if m_dt and pending_employee_id is not None:
                        pending_date = m_dt.group(1)
                        continue

                    m_tx = TRANSACTION_RE.match(line)
                    if m_tx and pending_employee_id is not None and pending_date:
                        # Flush previous receipt state if it's a new transaction
                        if current_tx_id is not None and str(current_tx_id) not in existing_tx_ids:
                            if any(v is not None for v in state.values()):
                                new_rows.append({"transaction_id": current_tx_id, **state})

                        current_tx_id       = int(m_tx.group(1))
                        in_totals_block     = False
                        in_payment_block    = False
                        pending_employee_id = None
                        pending_date        = None
                        state = {"subtotal": None, "tps": None, "tvq": None, "total": None, "arrondissement": None}
                        continue

                    if current_tx_id and str(current_tx_id) in existing_tx_ids:
                        skipped_tx += 1
                        continue

                    if SEPARATOR_RE.match(line):
                        in_totals_block  = True
                        in_payment_block = False
                        continue

                    if in_totals_block:
                        if SUBTOTAL_RE.match(line):
                            state["subtotal"] = parse_amount(line)
                        elif TPS_RE.match(line):
                            state["tps"] = parse_amount(line)
                        elif TVQ_RE.match(line):
                            state["tvq"] = parse_amount(line)
                        elif TOTAL_RE.match(line):
                            state["total"]   = parse_amount(line)
                            in_totals_block  = False
                            in_payment_block = True
                        continue

                    if in_payment_block:
                        if ARRONDISSEMENT_RE.match(line):
                            state["arrondissement"] = parse_amount(line)
                        elif TPS_NUMBER_RE.match(line):
                            in_payment_block = False

        # Final flush for the very last receipt in the PDF
        if current_tx_id is not None and str(current_tx_id) not in existing_tx_ids:
            if any(v is not None for v in state.values()):
                new_rows.append({"transaction_id": current_tx_id, **state})

    except Exception as e:
        print(f"   ❌ Error processing {pdf_path.name}: {e}")

# ============================================================
# 4. SAVE CSV
# ============================================================
if new_rows:
    new_df    = pd.DataFrame(new_rows)
    df_totals = pd.concat([df_totals.dropna(axis=1, how="all"), new_df], ignore_index=True)
    df_totals = df_totals.sort_values("transaction_id").reset_index(drop=True)
    df_totals.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")
    print(f"\n✅ Done! {len(new_rows)} new rows | {skipped_tx} skipped | {len(df_totals)} total")
else:
    print(f"\n✅ No new rows. CSV unchanged ({len(df_totals)} total).")
