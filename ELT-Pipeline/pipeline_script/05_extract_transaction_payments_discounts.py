# ============================================================
# SCRIPT 05 — EXTRACT TRANSACTION PAYMENTS & DISCOUNTS
# transaction_payments.csv + transaction_discounts.csv
#
# NOTE: This script is shared for portfolio and documentation
# purposes. Paths and restaurant-specific identifiers have
# been anonymized. The script will not run as-is.
#
# DEPENDENCY: Requires payment_type_table.csv and
# discount_type_table.csv to exist before running.
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

INPUT_DIR        = BASE_DIR / "INPUT"
OUTPUT_DIR       = BASE_DIR / "CSV"
OUTPUT_PAYMENTS  = OUTPUT_DIR / "transaction_payments.csv"
OUTPUT_DISCOUNTS = OUTPUT_DIR / "transaction_discounts.csv"
PAYMENT_REF      = OUTPUT_DIR / "payment_type_table.csv"
DISCOUNT_REF     = OUTPUT_DIR / "discount_type_table.csv"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Helpers ---
def normalize(text):
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()

def parse_amount(text):
    m = re.search(r'\x24(-?[0-9]+\.[0-9]{2})', text)
    return float(m.group(1)) if m else None

# --- Regex patterns ---
PAGE_HEADER_RE    = re.compile(r"^\d{1,2}/\d{2}/\d{2}\s+\d{2}:\d{2}\s+PAGE\s+\d+")
SERVER_LINE_RE    = re.compile(r"^(\d+)\.[A-Z][A-Z]+(?:\s|$)(?!.*\.)")
DATETIME_RE       = re.compile(r"^(\d{1,2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2})$")
TRANSACTION_RE    = re.compile(r"^(\d+)\s+\(\d+\)(?:\s+-\s+Table#(\d+))?")
SEPARATOR_RE      = re.compile(r"^={5,}")
SUBTOTAL_RE       = re.compile(r"^Sous-total")
TOTAL_RE          = re.compile(r"^Total\s+\x24")
ARRONDISSEMENT_RE = re.compile(r"^\d+\.ARRONDISSEMENT\s+\x24")
TPS_NUMBER_RE     = re.compile(r"^T\.P\.S\s+\d")
TPS_LINE_RE       = re.compile(r"^\d+\.T\.P\.S\s+\x24")
TVQ_LINE_RE       = re.compile(r"^\d+\.T\.V\.Q\s+\x24")
AMOUNT_LINE_RE    = re.compile(r"^\d+\..+\x24-?[0-9]+\.[0-9]{2}")

# ============================================================
# 1. LOAD REFERENCE TABLES (REQUIRED)
# ============================================================
if not PAYMENT_REF.exists():
    print(f"❌ Error: {PAYMENT_REF.name} not found!")
    sys.exit(1)

if not DISCOUNT_REF.exists():
    print(f"❌ Error: {DISCOUNT_REF.name} not found!")
    sys.exit(1)

df_pay_ref = pd.read_csv(PAYMENT_REF)
df_dis_ref = pd.read_csv(DISCOUNT_REF)
payment_lookup  = {normalize(row["payment_type"]): int(row["payment_id"]) for _, row in df_pay_ref.iterrows()}
discount_lookup = {normalize(row["discount_type"]): int(row["discount_id"]) for _, row in df_dis_ref.iterrows()}
print(f"✅ Loaded {len(payment_lookup)} payment types and {len(discount_lookup)} discount types.")

# ============================================================
# 2. LOAD EXISTING CSVs
# ============================================================
if OUTPUT_PAYMENTS.exists():
    df_payments     = pd.read_csv(OUTPUT_PAYMENTS)
    existing_pay_tx = set(df_payments["transaction_id"].astype(str).tolist())
else:
    df_payments     = pd.DataFrame(columns=["transaction_id", "payment_id", "amount"])
    existing_pay_tx = set()

if OUTPUT_DISCOUNTS.exists():
    df_discounts    = pd.read_csv(OUTPUT_DISCOUNTS)
    existing_dis_tx = set(df_discounts["transaction_id"].astype(str).tolist())
else:
    df_discounts    = pd.DataFrame(columns=["transaction_id", "discount_id", "amount"])
    existing_dis_tx = set()

# ============================================================
# 3. FIND ALL PDFs (EARLY EXIT IF EMPTY)
# ============================================================
pdf_files = sorted(INPUT_DIR.glob("*.pdf"))
if not pdf_files:
    print(f"⚠️  No PDF files found in: {INPUT_DIR}")
    sys.exit(0)

# ============================================================
# 4. PROCESSING
# ============================================================
new_pay_rows  = []
new_dis_rows  = []
skipped_pay   = 0
skipped_dis   = 0
not_found_pay = 0
not_found_dis = 0

for pdf_path in pdf_files:
    print(f"\n🔍 Processing: {pdf_path.name}")
    pay_rows_this_pdf   = 0
    dis_rows_this_pdf   = 0
    current_tx_id       = None
    pending_employee_id = None
    pending_date        = None
    in_discount_block   = False
    in_payment_block    = False

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                for line in text.splitlines():
                    line = line.strip()
                    if PAGE_HEADER_RE.match(line): continue
                    if RESTAURANT_HEADER in line: continue

                    m_server = SERVER_LINE_RE.match(line)
                    if m_server:
                        pending_employee_id = int(m_server.group(1))
                        pending_date        = None
                        in_discount_block   = False
                        in_payment_block    = False
                        continue

                    m_dt = DATETIME_RE.match(line)
                    if m_dt and pending_employee_id is not None:
                        pending_date = m_dt.group(1)
                        continue

                    m_tx = TRANSACTION_RE.match(line)
                    if m_tx and pending_employee_id is not None and pending_date:
                        current_tx_id       = int(m_tx.group(1))
                        in_discount_block   = False
                        in_payment_block    = False
                        pending_employee_id = None
                        pending_date        = None
                        continue

                    if current_tx_id is None: continue

                    if SEPARATOR_RE.match(line):
                        in_discount_block = True
                        in_payment_block  = False
                        continue

                    if SUBTOTAL_RE.match(line): continue
                    if TPS_LINE_RE.match(line) or TVQ_LINE_RE.match(line): continue

                    if TOTAL_RE.match(line):
                        in_discount_block = False
                        in_payment_block  = True
                        continue

                    if TPS_NUMBER_RE.match(line):
                        in_payment_block = False
                        continue

                    # --- Discount lines ---
                    if in_discount_block and AMOUNT_LINE_RE.match(line):
                        if line.endswith("FP"): continue
                        m = re.match(r"^\d+\.(.+?)\s+\x24", line)
                        if m:
                            norm_name   = normalize(m.group(1).strip())
                            amount      = parse_amount(line)
                            discount_id = discount_lookup.get(norm_name)
                            if discount_id is None:
                                not_found_dis += 1
                                continue
                            if str(current_tx_id) in existing_dis_tx:
                                skipped_dis += 1
                                continue
                            new_dis_rows.append({"transaction_id": current_tx_id, "discount_id": discount_id, "amount": amount})
                            dis_rows_this_pdf += 1

                    # --- Payment lines ---
                    if in_payment_block and AMOUNT_LINE_RE.match(line):
                        if ARRONDISSEMENT_RE.match(line): continue
                        m = re.match(r"^\d+\.(.+?)\s+\x24", line)
                        if m:
                            norm_name  = normalize(m.group(1).strip())
                            amount     = parse_amount(line)
                            payment_id = payment_lookup.get(norm_name)
                            if payment_id is None:
                                not_found_pay += 1
                                continue
                            if str(current_tx_id) in existing_pay_tx:
                                skipped_pay += 1
                                continue
                            new_pay_rows.append({"transaction_id": current_tx_id, "payment_id": payment_id, "amount": amount})
                            pay_rows_this_pdf += 1

        print(f"   → {pay_rows_this_pdf} payment rows | {dis_rows_this_pdf} discount rows")

    except Exception as e:
        print(f"   ❌ Error processing {pdf_path.name}: {e}")

# ============================================================
# 5. SAVE CSVs
# ============================================================
if new_pay_rows:
    new_df      = pd.DataFrame(new_pay_rows)
    df_payments = pd.concat([df_payments.dropna(axis=1, how="all"), new_df], ignore_index=True)
    df_payments = df_payments.sort_values("transaction_id").reset_index(drop=True)
    df_payments.to_csv(OUTPUT_PAYMENTS, index=False, encoding="utf-8")
    print(f"\n✅ Payments — {len(new_pay_rows)} new | {skipped_pay} skipped | {len(df_payments)} total")

if new_dis_rows:
    new_df       = pd.DataFrame(new_dis_rows)
    df_discounts = pd.concat([df_discounts.dropna(axis=1, how="all"), new_df], ignore_index=True)
    df_discounts = df_discounts.sort_values("transaction_id").reset_index(drop=True)
    df_discounts.to_csv(OUTPUT_DISCOUNTS, index=False, encoding="utf-8")
    print(f"✅ Discounts — {len(new_dis_rows)} new | {skipped_dis} skipped | {len(df_discounts)} total")
