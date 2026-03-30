# ============================================================
# SCRIPT 08 — WEATHER FETCHER (Open-Meteo API)
# Fetches historical + 7-day forecast weather data.
# Triggered automatically by 07_run_pipeline.py.
#
# NOTE: This script is shared for portfolio and documentation
# purposes. Paths and coordinates have been anonymized.
# The script will not run as-is.
# ============================================================

import csv
import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

# ============================================================
# CONFIGURATION
# Replace BASE_DIR with the actual path on your machine.
# Replace LATITUDE and LONGITUDE with your restaurant's
# coordinates. Adjust TIMEZONE to match your region.
# DEFAULT_START_DATE is used only on the very first run
# when no weather_last_fetch.txt exists yet.
# ============================================================
BASE_DIR  = Path(r"C:/path/to/your/pipeline/folder")

LATITUDE  = 00.0000   # Replace with your latitude
LONGITUDE = 00.0000   # Replace with your longitude (negative = West)
TIMEZONE  = "America/Toronto"  # Adjust to your timezone

SCRIPT_DIR = BASE_DIR / "SCRIPTS"
CSV_DIR    = BASE_DIR / "CSV"
DATE_FILE  = SCRIPT_DIR / "weather_last_fetch.txt"

DEFAULT_START_DATE = date(2020, 1, 1)

# ============================================================
# HELPERS
# ============================================================

def check_internet(timeout: int = 5) -> bool:
    """
    Return True if ANY of the test endpoints responds.
    Tries multiple hosts so a VPN or blocked domain doesn't
    cause a false negative.
    """
    test_urls = [
        "https://api.open-meteo.com",
        "https://dns.google",
        "https://one.one.one.one",
        "https://www.apple.com/library/test/success.html",
    ]
    for url in test_urls:
        try:
            urllib.request.urlopen(url, timeout=timeout)
            return True
        except Exception:
            continue
    return False


def read_last_date() -> date:
    """Return the start date for the historical fetch."""
    if DATE_FILE.exists():
        text = DATE_FILE.read_text().strip()
        try:
            last = date.fromisoformat(text)
            # Start the day AFTER the last fetched date to avoid duplicates
            return last + timedelta(days=1)
        except ValueError:
            pass
    return DEFAULT_START_DATE


def write_today_date() -> None:
    """Persist today's date so the next run knows where to start."""
    DATE_FILE.write_text(date.today().isoformat())


def build_url(base: str, params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{query}"


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode())


# ============================================================
# API CALLS
# ============================================================

COMMON_PARAMS = {
    "latitude"       : LATITUDE,
    "longitude"      : LONGITUDE,
    "timezone"       : TIMEZONE,
    "wind_speed_unit": "kmh",
}

DAILY_VARS = "precipitation_sum,temperature_2m_max,temperature_2m_min,windspeed_10m_max,weathercode"

CSV_HEADERS = [
    "date",
    "precipitation_mm",
    "temperature_max_c",
    "temperature_min_c",
    "windspeed_max_kmh",
    "weather_code",
]


def fetch_historical(start: date, end: date) -> list[dict]:
    """Fetch daily historical weather from Open-Meteo archive API."""
    url = build_url("https://archive-api.open-meteo.com/v1/archive", {
        **COMMON_PARAMS,
        "start_date": start.isoformat(),
        "end_date"  : end.isoformat(),
        "daily"     : DAILY_VARS,
    })
    data = fetch_json(url)
    return zip_daily(data["daily"])


def fetch_forecast() -> list[dict]:
    """Fetch 7-day forecast from Open-Meteo forecast API."""
    url = build_url("https://api.open-meteo.com/v1/forecast", {
        **COMMON_PARAMS,
        "forecast_days": 7,
        "daily"        : DAILY_VARS,
    })
    data = fetch_json(url)
    return zip_daily(data["daily"])


def zip_daily(daily: dict) -> list[dict]:
    """Convert parallel lists from the API into a list of row dicts."""
    rows = []
    for i, d in enumerate(daily["time"]):
        rows.append({
            "date"             : d,
            "precipitation_mm" : daily["precipitation_sum"][i],
            "temperature_max_c": daily["temperature_2m_max"][i],
            "temperature_min_c": daily["temperature_2m_min"][i],
            "windspeed_max_kmh": daily["windspeed_10m_max"][i],
            "weather_code"     : daily["weathercode"][i],
        })
    return rows


# ============================================================
# CSV WRITER
# ============================================================

def write_csv(path: Path, rows: list[dict], append: bool = False) -> None:
    """
    Write rows to CSV.
    append=True adds new rows without rewriting the header.
    append=False overwrites the file completely (used for forecast).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists() and append

    with open(path, "a" if append else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)

    total = sum(1 for _ in open(path, encoding="utf-8")) - 1
    print(f"   ✅ Saved: {path.name}  ({len(rows)} new rows, {total} total)")


# ============================================================
# MAIN
# ============================================================

def run():
    print("=" * 60)
    print("  SCRIPT 08 — WEATHER FETCHER")
    print("=" * 60)

    # --- Internet check ---
    print("\n🌐 Checking internet connection...")
    if not check_internet():
        print("   ⚠️  No internet access. Skipping weather fetch.")
        sys.exit(0)
    print("   ✅ Internet OK")

    today = date.today()

    # --- Historical fetch ---
    hist_start = read_last_date()
    hist_end   = today
    hist_csv   = CSV_DIR / "weather_historical.csv"

    if hist_start > hist_end:
        print(f"\n📅 Historical data already up to date (last fetch: {hist_end}). Skipping.")
    else:
        print(f"\n📅 Fetching historical weather: {hist_start} → {hist_end}")
        try:
            hist_rows = fetch_historical(hist_start, hist_end)
            write_csv(hist_csv, hist_rows, append=True)
        except Exception as e:
            print(f"   ❌ Historical fetch failed: {e}")
            sys.exit(1)

    # --- Forecast fetch ---
    forecast_start = today + timedelta(days=1)
    forecast_end   = today + timedelta(days=7)
    forecast_csv   = CSV_DIR / "weather_forecast.csv"
    print(f"\n🔮 Fetching 7-day forecast: {forecast_start} → {forecast_end}")
    try:
        forecast_rows = fetch_forecast()
        write_csv(forecast_csv, forecast_rows)
    except Exception as e:
        print(f"   ❌ Forecast fetch failed: {e}")
        sys.exit(1)

    # --- Persist today's date ---
    write_today_date()
    print(f"\n📝 Last-fetch date saved → {DATE_FILE.name}")

    print("\n" + "=" * 60)
    print("  ✅ WEATHER FETCH COMPLETE")
    print("=" * 60)
    sys.exit(0)


if __name__ == "__main__":
    run()
