# ============================================================
# SCRIPT 07 — MASTER PIPELINE RUNNER
# Runs all 6 extraction scripts + weather fetch + Supabase import.
# Drop PDFs in INPUT folder, double-click to run.
#
# NOTE: This script is shared for portfolio and documentation
# purposes. Paths have been anonymized. The script will not
# run as-is.
# ============================================================
import shutil
import subprocess
import sys
from pathlib import Path

# ============================================================
# CONFIGURATION
# Replace BASE_DIR with the actual path on your machine.
# ============================================================
BASE_DIR      = Path(r"C:/path/to/your/pipeline/folder")
SCRIPT_DIR    = BASE_DIR / "SCRIPTS"
INPUT_DIR     = BASE_DIR / "INPUT"
CONVERTED_DIR = BASE_DIR / "CONVERT"

CONVERTED_DIR.mkdir(parents=True, exist_ok=True)

# List of scripts to execute in sequence
EXTRACTION_SCRIPTS = [
    "01_extract_lookup_tables.py",
    "02_extract_transaction_table.py",
    "03_extract_transaction_items.py",
    "04_extract_transaction_totals.py",
    "05_extract_transaction_payments_discounts.py",
    "06_extract_item_categories.py",
]

WEATHER_SCRIPT = "08_fetch_weather.py"
IMPORT_SCRIPT  = "09_import_to_supabase.py"


def run_script(script_path: Path) -> bool:
    """Run a Python script as a subprocess. Returns True if it succeeded."""
    if not script_path.exists():
        print(f"❌ Error: Script not found at {script_path}")
        return False

    result = subprocess.run([sys.executable, str(script_path)])

    if result.returncode != 0:
        print(f"\n❌ {script_path.name} failed with error code {result.returncode}.")
        return False

    return True


def run_pipeline():
    print("=" * 60)
    print("  RESTAURANT FORECASTING — EXTRACTION PIPELINE")
    print("=" * 60)

    # --------------------------------------------------------
    # PHASE 1 — PDF Extraction (scripts 01–06)
    # --------------------------------------------------------
    pdf_files = sorted(INPUT_DIR.glob("*.pdf"))

    if not pdf_files:
        print(f"\n⚠️  No PDF files found in INPUT folder. Skipping extraction scripts.")
        extraction_ok = True
    else:
        print(f"\n📄 {len(pdf_files)} PDF(s) ready to process:")
        for p in pdf_files:
            print(f"   • {p.name}")

        extraction_ok = True
        for script in EXTRACTION_SCRIPTS:
            script_path = SCRIPT_DIR / script

            print(f"\n{'=' * 60}")
            print(f"  ▶ Running {script}")
            print(f"{'=' * 60}\n")

            if not run_script(script_path):
                extraction_ok = False
                break

    # --------------------------------------------------------
    # PHASE 2 — Move PDFs (only if extraction succeeded)
    # --------------------------------------------------------
    if extraction_ok and pdf_files:
        print(f"\n{'=' * 60}")
        print("  📦 Moving PDFs to CONVERT folder...")
        print(f"{'=' * 60}")
        for pdf_path in pdf_files:
            dest = CONVERTED_DIR / pdf_path.name

            if dest.exists():
                dest = CONVERTED_DIR / f"{pdf_path.stem}_duplicate{pdf_path.suffix}"

            try:
                shutil.move(str(pdf_path), str(dest))
                print(f"   ✅ Moved: {pdf_path.name} → CONVERT/")
            except Exception as e:
                print(f"   ⚠️  Could not move {pdf_path.name}: {e}")

    elif not extraction_ok:
        print(f"\n⚠️  Extraction stopped due to error. PDFs remain in INPUT folder.")

    # --------------------------------------------------------
    # PHASE 3 — Weather Fetch (script 08) — always runs
    # --------------------------------------------------------
    print(f"\n{'=' * 60}")
    print(f"  ▶ Running {WEATHER_SCRIPT}")
    print(f"{'=' * 60}\n")

    weather_script_path = SCRIPT_DIR / WEATHER_SCRIPT
    run_script(weather_script_path)
    # Weather failures are non-blocking — pipeline continues

    # --------------------------------------------------------
    # PHASE 4 — Supabase Import (script 09) — always runs last
    # --------------------------------------------------------
    print(f"\n{'=' * 60}")
    print(f"  ▶ Running {IMPORT_SCRIPT}")
    print(f"{'=' * 60}\n")

    import_script_path = SCRIPT_DIR / IMPORT_SCRIPT
    import_ok = run_script(import_script_path)

    # --------------------------------------------------------
    # FINAL SUMMARY
    # --------------------------------------------------------
    print(f"\n{'=' * 60}")
    if extraction_ok and import_ok:
        print("  🎉 PIPELINE COMPLETE — All CSVs imported to Supabase!")
    elif extraction_ok and not import_ok:
        print("  ⚠️  EXTRACTION OK — but Supabase import failed (see above)")
    else:
        print("  ⚠️  PIPELINE FINISHED WITH ERRORS (see above)")
    print(f"{'=' * 60}")

    input("\nWorkflow finished. Press Enter to exit...")


if __name__ == "__main__":
    run_pipeline()
