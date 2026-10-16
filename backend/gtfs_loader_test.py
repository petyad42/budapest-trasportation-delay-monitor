"""Unit tests for :mod:`backend.gtfs_loader` (Requirement 9.1)."""
from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from backend.gtfs_loader import GtfsLoader, GtfsLoadError, GtfsTables

# --- Small fixture GTFS feed -------------------------------------------------

_ROUTES = (
    "route_id,route_short_name,route_type,route_color\n"
    "R1,4,0,FFD800\n"       # tram
    "R2,7,3,009EE3\n"       # bus
    "M2,M2,1,E41F18\n"      # metro
)
_TRIPS = (
    "trip_id,route_id,service_id,direction_id,shape_id\n"
    "T1,R1,WD,0,S1\n"
    "T2,R2,WD,1,S2\n"
    "T3,M2,WD,0,S3\n"
)
_STOPS = (
    "stop_id,stop_name,stop_lat,stop_lon\n"
    "ST1,Deak Ferenc ter,47.4979,19.0546\n"
    "ST2,Astoria,47.4939,19.0596\n"
    "ST3,Kalvin ter,47.4890,19.0611\n"
)
_STOP_TIMES = (
    "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
    "T1,ST1,08:00:00,08:00:30,1\n"
    "T1,ST2,08:04:00,08:04:30,2\n"
    "T2,ST2,09:00:00,09:00:30,1\n"
    "T2,ST3,09:05:00,09:05:30,2\n"
)
_CALENDAR = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\n"
    "WD,1,1,1,1,1,0,0,20240101,20241231\n"
)
_CALENDAR_DATES = (
    "service_id,date,exception_type\n"
    "WD,20240101,2\n"
)


def _build_zip(members: dict[str, str]) -> bytes:
    """Build an in-memory GTFS zip from ``{filename: csv_text}``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _full_feed_members() -> dict[str, str]:
    return {
        "routes.txt": _ROUTES,
        "trips.txt": _TRIPS,
        "stops.txt": _STOPS,
        "stop_times.txt": _STOP_TIMES,
        "calendar.txt": _CALENDAR,
    }


@pytest.fixture()
def fixture_zip(tmp_path):
    """Path to a small, valid GTFS zip on disk."""
    path = tmp_path / "budapest_gtfs.zip"
    path.write_bytes(_build_zip(_full_feed_members()))
    return path


# --- Happy path --------------------------------------------------------------

def test_load_returns_gtfs_tables(fixture_zip):
    tables = GtfsLoader().load(fixture_zip)

    assert isinstance(tables, GtfsTables)
    for df in (tables.routes, tables.trips, tables.stops,
               tables.stop_times, tables.calendar):
        assert isinstance(df, pd.DataFrame)


def test_load_parses_expected_rows_and_columns(fixture_zip):
    tables = GtfsLoader().load(fixture_zip)

    assert len(tables.routes) == 3
    assert len(tables.trips) == 3
    assert len(tables.stops) == 3
    assert len(tables.stop_times) == 4
    assert {"route_id", "route_short_name", "route_type"} <= set(tables.routes.columns)
    assert {"trip_id", "route_id"} <= set(tables.trips.columns)
    assert {"stop_id", "stop_lat", "stop_lon"} <= set(tables.stops.columns)


def test_id_columns_preserved_as_strings(fixture_zip):
    tables = GtfsLoader().load(fixture_zip)
    # route_id / stop_id must stay strings so leading zeros are not lost.
    assert tables.routes["route_id"].map(type).eq(str).all()
    assert tables.stops["stop_id"].map(type).eq(str).all()


def test_numeric_columns_are_coerced(fixture_zip):
    tables = GtfsLoader().load(fixture_zip)

    assert pd.api.types.is_numeric_dtype(tables.routes["route_type"])
    assert pd.api.types.is_numeric_dtype(tables.stops["stop_lat"])
    assert pd.api.types.is_numeric_dtype(tables.stops["stop_lon"])
    assert pd.api.types.is_numeric_dtype(tables.stop_times["stop_sequence"])
    assert tables.routes.loc[tables.routes["route_id"] == "R1", "route_type"].iloc[0] == 0


def test_accepts_string_path(fixture_zip):
    tables = GtfsLoader().load(str(fixture_zip))
    assert len(tables.routes) == 3


# --- calendar fallback -------------------------------------------------------

def test_calendar_dates_used_when_calendar_absent(tmp_path):
    members = _full_feed_members()
    del members["calendar.txt"]
    members["calendar_dates.txt"] = _CALENDAR_DATES
    path = tmp_path / "feed.zip"
    path.write_bytes(_build_zip(members))

    tables = GtfsLoader().load(path)

    assert not tables.calendar.empty
    assert "exception_type" in tables.calendar.columns


def test_calendar_empty_when_neither_present(tmp_path):
    members = _full_feed_members()
    del members["calendar.txt"]
    path = tmp_path / "feed.zip"
    path.write_bytes(_build_zip(members))

    tables = GtfsLoader().load(path)

    assert isinstance(tables.calendar, pd.DataFrame)
    assert tables.calendar.empty


# --- Error handling (Requirement 9.3) ---------------------------------------

def test_missing_zip_raises_typed_error(tmp_path):
    missing = tmp_path / "does_not_exist.zip"
    with pytest.raises(GtfsLoadError):
        GtfsLoader().load(missing)


def test_corrupt_zip_raises_typed_error(tmp_path):
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"this is not a zip file")
    with pytest.raises(GtfsLoadError):
        GtfsLoader().load(corrupt)


def test_missing_required_file_raises_typed_error(tmp_path):
    members = _full_feed_members()
    del members["stops.txt"]  # required core table
    path = tmp_path / "incomplete.zip"
    path.write_bytes(_build_zip(members))

    with pytest.raises(GtfsLoadError):
        GtfsLoader().load(path)


def test_directory_path_raises_typed_error(tmp_path):
    with pytest.raises(GtfsLoadError):
        GtfsLoader().load(tmp_path)
