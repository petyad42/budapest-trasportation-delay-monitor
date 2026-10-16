"""Central configuration for the Budapest Transit Monitor backend.

All values are read from environment variables (optionally loaded from a
``.env`` file) with sane defaults so the app runs out of the box. Only free
data sources are referenced here (BKK GTFS / GTFS-RT and Open-Meteo), in line
with the no-paid-API constraint (Requirement 9.5).
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv is optional; the app still works with plain env vars.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is a convenience only.
    pass


def _get_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# --- Project paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
FRONTEND_DIR = BASE_DIR / "frontend"

GTFS_ZIP_PATH = Path(_get_str("GTFS_ZIP_PATH", str(DATA_DIR / "budapest_gtfs.zip")))
MODEL_PATH = Path(_get_str("MODEL_PATH", str(DATA_DIR / "delay_model.joblib")))

# --- External data sources (all free, no paid APIs) ---
# BKK GTFS-Realtime needs a free API key; leaving it empty runs mock mode.
BKK_API_KEY = _get_str("BKK_API_KEY", "")

GTFS_STATIC_URL = _get_str(
    "GTFS_STATIC_URL",
    "https://bkk.hu/gtfs/budapest_gtfs.zip",
)
GTFS_RT_TRIP_UPDATES_URL = _get_str(
    "GTFS_RT_TRIP_UPDATES_URL",
    "https://go.bkk.hu/api/query/v1/ws/gtfs-rt/full/TripUpdates.pb",
)
GTFS_RT_VEHICLE_POSITIONS_URL = _get_str(
    "GTFS_RT_VEHICLE_POSITIONS_URL",
    "https://go.bkk.hu/api/query/v1/ws/gtfs-rt/full/VehiclePositions.pb",
)

# Open-Meteo current-weather endpoint (no API key required).
OPEN_METEO_URL = _get_str(
    "OPEN_METEO_URL",
    "https://api.open-meteo.com/v1/forecast",
)

# --- Budapest default coordinates (fallback when geolocation unavailable) ---
CITY_LAT = _get_float("CITY_LAT", 47.4979)
CITY_LON = _get_float("CITY_LON", 19.0402)

# --- Polling / freshness ---
# How often the background poller fetches a GTFS-RT snapshot (seconds).
POLL_INTERVAL_SECONDS = _get_int("POLL_INTERVAL_SECONDS", 30)

# A snapshot older than this many seconds is considered stale and flips the
# data-status indicator to MOCK (design: ~2 polling intervals by default).
STALENESS_THRESHOLD_SECONDS = _get_int(
    "STALENESS_THRESHOLD_SECONDS",
    POLL_INTERVAL_SECONDS * 2,
)

# Timeout applied to all outbound HTTP calls (seconds).
HTTP_TIMEOUT_SECONDS = _get_float("HTTP_TIMEOUT_SECONDS", 10.0)

# --- Server ---
API_HOST = _get_str("API_HOST", "0.0.0.0")
API_PORT = _get_int("API_PORT", 8000)

# Whether we have a usable realtime key. Without one, run mock mode so nothing
# breaks (Requirements 1.3, 9.3, 10).
MOCK_MODE = not bool(BKK_API_KEY)
