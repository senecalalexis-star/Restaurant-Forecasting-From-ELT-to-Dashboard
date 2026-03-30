# ============================================================
# SCRIPT 06 — EXTRACT ITEM CATEGORIES & UPDATE item_id_table
#
# NOTE: This script is shared for portfolio and documentation
# purposes. Paths and restaurant-specific identifiers have
# been anonymized. The script will not run as-is.
#
# This script reads a separate category sales report (different
# layout from the transaction report) and does three things:
#   1. Builds the category reference table
#   2. Maps each item to its category
#   3. Writes category IDs back into item_id_table.csv
#
# Item names may differ slightly between the two report types.
# Fuzzy prefix matching (min 8 chars) reconciles them.
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
# The strings below must match the section headers printed
# in your POS category sales report.
# ============================================================
BASE_DIR          = Path(r"C:/path/to/your/pipeline/folder")
RESTAURANT_HEADER = "RESTAURANT NAME"
POS_TOOL_HEADER   = "POS SYSTEM NAME"
REPORT_HEADER     = "ITEM DETAIL REPORT HEADER"

# Section markers in the category report
GLOBAL_SECTION_START  = "Global item sales section header"
GLOBAL_SECTION_END    = "Grand total marker"
EMPLOYEE_SECTION_START = "Per-employee section header"

CATEGORY_DIR  = BASE_DIR / "CATEGORY REFERENCE"
OUTPUT_DIR    = BASE_DIR / "CSV"

CATEGORY_FILE = OUTPUT_DIR / "category_table.csv"
ITEM_CAT_FILE = OUTPUT_DIR / "item_category.csv"
ITEM_FILE     = OUTPUT_DIR / "item_id_table.csv"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Normalization ---
def normalize(text):
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper().strip()

# --- Regex ---
PAGE_HEADER_RE  = re.compile(r"^\d{1,2}/\d{2}/\d{2}\s+\d{2}:\d{2}\s+PAGE\s+\d+")
GLOBAL_START_RE = re.compile(rf"^{re.escape(GLOBAL_SECTION_START)}")
GLOBAL_END_RE   = re.compile(rf"^{re.escape(GLOBAL_SECTION_END)}")
EMPLOYEE_RE     = re.compile(rf"^{re.escape(EMPLOYEE_SECTION_START)}")
CATEGORY_RE     = re.compile(r"^(\d+)\.([A-Z].+)$")
ITEM_LINE_RE    = re.compile(r"^(.+?)\s+\d+(?:\.\d+)?\s+\x24[\d\.]+\s+[\d\.]+%")

# ============================================================
# HELPERS
# ============================================================
def is_noise_line(raw):
    if PAGE_HEADER_RE.match(raw): return True
    if RESTAURANT_HEADER in raw: return True
    if POS_TOOL_HEADER in raw: return True
    if REPORT_HEADER in raw: return True
    if re.match(r"^\d{1,2}/\d{2}/\d{2}\s+@", raw): return True
    if raw.startswith("Quantite"): return True
    if raw.startswith("Total"): return True
    return False

def fuzzy_match(norm_name, item_lookup):
    """
    Attempts exact match first, then prefix-based fuzzy match.
    Minimum 8 characters required for fuzzy matching to avoid
    false positives on short item names.
    """
    if norm_name in item_lookup:
        return item_lookup[norm_name]
    if len(norm_name) >= 8:
        for key, idx in item_lookup.items():
            if key.startswith(norm_name):
                return idx
        for key, idx in item_lookup.items():
            if len(key) >= 8 and norm_name.startswith(key):
                return idx
    return None

# ============================================================
# 1. VALIDATE CATEGORY FOLDER
# ============================================================
if not CATEGORY_DIR.exists():
    print(f"❌ Error: CATEGORY REFERENCE folder not found:\n  {CATEGORY_DIR}")
    sys.exit(1)

cat_pdf_files = sorted(CATEGORY_DIR.glob("*.pdf"))
if not cat_pdf_files:
    print(f"⚠️ No PDFs in CATEGORY REFERENCE folder. Skipping category update.")
    sys.exit(0)

# ============================================================
# STEP 1 — BUILD category_table.csv
# ============================================================
print("=" * 60)
print("  STEP 1 — Building category_table.csv")
print("=" * 60)

if CATEGORY_FILE.exists():
    df_cat = pd.read_csv(CATEGORY_FILE)
    existing_cat_names = set(normalize(n) for n in df_cat["category_name"])
    print(f"✅ Loaded {len(df_cat)} existing categories.")
else:
    df_cat = pd.DataFrame(columns=["category_id", "category_name"])
    existing_cat_names = set()
    print("🆕 Starting fresh category_table.csv")

new_cats = []
for pdf_path in cat_pdf_files:
    in_global = False
    done      = False
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                if done: break
                text = page.extract_text()
                if not text: continue
                for line in text.splitlines():
                    raw = line.strip()
                    if is_noise_line(raw): continue
                    if GLOBAL_START_RE.match(raw):
                        in_global = True
                        continue
                    if GLOBAL_END_RE.match(raw) or EMPLOYEE_RE.match(raw):
                        in_global = False
                        done = True
                        break
                    if not in_global: continue
                    m = CATEGORY_RE.match(raw)
                    if m:
                        cat_id   = int(m.group(1))
                        cat_name = normalize(m.group(2).strip())
                        if cat_name not in existing_cat_names:
                            existing_cat_names.add(cat_name)
                            new_cats.append({"category_id": cat_id, "category_name": cat_name})
    except Exception as e:
        print(f"❌ Error reading {pdf_path.name}: {e}")

if new_cats:
    df_cat = pd.concat([df_cat, pd.DataFrame(new_cats)], ignore_index=True)
    df_cat = df_cat.sort_values("category_id").reset_index(drop=True)
    df_cat.to_csv(CATEGORY_FILE, index=False, encoding="utf-8")
    print(f"✅ {len(new_cats)} new categories added.")

cat_id_lookup = {normalize(r["category_name"]): int(r["category_id"]) for _, r in df_cat.iterrows()}

# ============================================================
# STEP 2 — BUILD item_category.csv
# ============================================================
print(f"\n{'=' * 60}")
print("  STEP 2 — Building item_category.csv")
print(f"{'=' * 60}")

if ITEM_CAT_FILE.exists():
    df_ic = pd.read_csv(ITEM_CAT_FILE)
    existing_ic = set(zip(df_ic["item_name_raw"], df_ic["category_id"]))
else:
    df_ic = pd.DataFrame(columns=["item_name_raw", "category_id"])
    existing_ic = set()

new_ic_rows = []
for pdf_path in cat_pdf_files:
    in_global      = False
    done           = False
    current_cat_id = None
    count          = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            if done: break
            text = page.extract_text()
            if not text: continue
            for line in text.splitlines():
                raw = line.strip()
                if is_noise_line(raw): continue
                if GLOBAL_START_RE.match(raw):
                    in_global = True
                    continue
                if GLOBAL_END_RE.match(raw) or EMPLOYEE_RE.match(raw):
                    in_global = False
                    done = True
                    break
                if not in_global: continue
                m_cat = CATEGORY_RE.match(raw)
                if m_cat:
                    cat_name       = normalize(m_cat.group(2).strip())
                    current_cat_id = cat_id_lookup.get(cat_name)
                    continue
                if current_cat_id is None: continue
                m_item = ITEM_LINE_RE.match(raw)
                if not m_item: continue
                item_name_raw = normalize(m_item.group(1).strip())
                if (item_name_raw, current_cat_id) not in existing_ic:
                    existing_ic.add((item_name_raw, current_cat_id))
                    new_ic_rows.append({"item_name_raw": item_name_raw, "category_id": current_cat_id})
                    count += 1
    print(f"🔍 {pdf_path.name}: {count} new mappings")

if new_ic_rows:
    df_ic = pd.concat([df_ic, pd.DataFrame(new_ic_rows)], ignore_index=True)
    df_ic.to_csv(ITEM_CAT_FILE, index=False, encoding="utf-8")

# ============================================================
# STEP 3 — MATCH TO item_id_table.csv
# ============================================================
print(f"\n{'=' * 60}")
print("  STEP 3 — Matching to item_id_table.csv")
print(f"{'=' * 60}")

if not ITEM_FILE.exists():
    print("❌ Error: item_id_table.csv not found!")
    sys.exit(1)

df_items = pd.read_csv(ITEM_FILE)
if "category_id" not in df_items.columns:
    df_items["category_id"] = pd.NA

item_lookup = {row["item_name"]: idx for idx, row in df_items.iterrows()}
matched = 0

for _, row in df_ic.iterrows():
    idx = fuzzy_match(row["item_name_raw"], item_lookup)
    if idx is not None and pd.isna(df_items.at[idx, "category_id"]):
        df_items.at[idx, "category_id"] = int(row["category_id"])
        matched += 1

df_items.to_csv(ITEM_FILE, index=False, encoding="utf-8")
print(f"✅ Successfully matched {matched} items to categories.")
