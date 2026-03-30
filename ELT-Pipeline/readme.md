# Restaurant Forecasting — From ELT to Dashboard

A full production ELT pipeline and forecasting system built to help a seasonal restaurant better manage their resources.

Built from scratch — no existing database, no API, no CSV files. Raw print files from a POS system with no export feature are parsed by a suite of Python scripts, loaded into a structured PostgreSQL database on Supabase, transformed with dbt, and fed into a Prophet forecasting model. The full pipeline runs automatically every week via GitHub Actions.

**Stack:** Python · PostgreSQL · Supabase · dbt · Prophet · Power BI · GitHub Actions

---

## Note on reproducibility

This project was built for a specific restaurant and is shared for documentation and portfolio purposes. File paths, database credentials, and some business logic are specific to this environment. Server names have been anonymized. The code will not run as-is if downloaded — it is intended to be read and understood, not executed directly.

---

## Architecture

![Pipeline architecture](ELT-Pipeline/docs/pipeline_architecture.png)

---

## Data sources

![Data sources flow](ELT-Pipeline/docs/data_sources_flow.png)

| Source | Format | Description |
|---|---|---|
| Veloce POS — transaction report | .PRN → PDF | Chronological list of every finalized check, parsed by scripts 01–05 |
| Veloce POS — category report | .PRN → PDF | Weekly item sales by category, parsed by script 06 |
| Event data | .CSV | Restaurant events maintained manually by the manager — dates, names, estimated attendance |
| Weather | Open-Meteo API | Historical weather + 7-day forecast, fetched automatically on every run |

---

## Extraction

The transaction report is parsed by five scripts in strict sequence. Each extracts a different slice of the same receipt and feeds a different table in the database.

![Annotated receipt](ELT-Pipeline/docs/annotated_receipt.png)

---

## Script breakdown

| Script | Purpose | Input | Output |
|---|---|---|---|
| `01_extract_lookup_tables.py` | Builds item and employee reference tables | Transaction report PDF | `item_id_table.csv`, `employee_table.csv` |
| `02_extract_transaction_table.py` | Extracts transaction headers | Transaction report PDF | `transaction_table.csv` |
| `03_extract_transaction_items.py` | Extracts line items per transaction | Transaction report PDF + `item_id_table.csv` | `transaction_items.csv` |
| `04_extract_transaction_totals.py` | Extracts totals block | Transaction report PDF | `transaction_totals.csv` |
| `05_extract_transaction_payments_discounts.py` | Extracts payments and discounts | Transaction report PDF | `transaction_payments.csv`, `transaction_discounts.csv` |
| `06_extract_item_categories.py` | Maps items to categories from category report | Category report PDF + `item_id_table.csv` | `category_table.csv`, `item_category.csv` |
| `07_run_pipeline.py` | Master runner — executes scripts 01–06 in sequence | All PDFs in input folder | Triggers full extraction chain |
| `08_fetch_weather.py` | Fetches weather data from Open-Meteo API | API call | `weather_historical.csv`, `weather_forecast.csv` |
| `09_import_to_supabase.py` | Loads all CSVs into Supabase | All CSVs in output folder | Data loaded into raw schemas |

---

## Database schema

The raw layer is organized into three schemas. Each has a single responsibility — nothing is transformed or filtered at this stage.

### raw_reference — lookup tables
![raw_reference schema](ELT-Pipeline/docs/raw_reference_schema.png)

### raw_transaction — operational data
![raw_transaction schema](ELT-Pipeline/docs/raw_transaction_schema.png)

### raw_weather — external data
![raw_weather schema](ELT-Pipeline/docs/raw_weather_schema.png)

The transformation schemas (`stg`, `features`, `forecast`, `mart`) are documented in the dbt README.

---

## Design decisions

**ELT over ETL**
Data is loaded into Supabase raw and unmodified before any transformation happens. This preserves the source of truth and means every business rule decision is made in one place — dbt — where it can be versioned and tested.

**Nothing is thrown away**
Hotel transactions, gift card payments, register numbers, rounding adjustments, previous forecast weather — all of it lands in the raw layer untouched. Data that seems irrelevant today might answer a question we haven't thought of yet.

**Three load strategies for three table types**
Reference tables use `INSERT ... ON CONFLICT DO NOTHING` — safe to re-run at any time. Transaction tables are appended then moved to a dated archive folder — the file's absence prevents double-loads. Weather uses a `weather_last_fetch.txt` tracking file — only rows newer than the last loaded date are inserted each run.

**Modular scripts, safe re-runs**
Each script is independent and idempotent. If the pipeline breaks, the console output points to exactly which script failed. Fixing the issue and re-running is all it takes — no duplicates, no data corruption.

**The employee name problem**
The first version treated the employee reference as a simple lookup — extracting names directly from each transaction. In production we discovered that Veloce reassigns names over time: the same employee ID can appear under a different name in a later report. The schema was updated to capture both ID and name at extraction time, preserving the real historical record regardless of what Veloce currently shows.
