"""Unit tests for :mod:`backend.gtfs_repository` (Requirements 2.1, 2.2, 2.4, 4.2, 4.3).

Focus areas called out by task 4 and the design test plan:
  * stops_for_line scoping (a line returns only its own stops) (R4.2, R4.3)
  * mode/color mapping via the centralized colors module (R2.2)
  * stops_near haversine radius (R2.1) and route_shape (R2.4)
  * graceful mock fallback when the GTFS zip cannot be loaded (R9.3, R10.3)
"""
from __future__ import annotations

import io
import zipfile

import pandas as pd
import pytest

from backend import colors
from backend.gtfs_loader import GtfsLoader
from backend.gtfs_repository import GtfsRepository, haversine_m
from backend.models import Mode


# --- Fixture GTFS feed with two distinct lines sharing one stop --------------
#
# Line R_TRAM (tram, route_type 0): STA -> STB -> STC
# Line R_BUS  (bus,  route_type 3): STB -> STD          (shares STB with tram)
# Line M2     (metro, route_type 1, route_color E41F18): STA -> STE
#
# STA/STB are near a common origin; STF is far away (used for radius tests).

_ROUTES = (
    "route_id,route_short_name,route_type,route_color\n"
    "R_TRAM,4,0,FFD800\n"
    "R_BUS,7,3,009EE3\n"
    "M2,M2,1,E41F18\n"
    "R_HEV,H5,109,00BBBB\n"   # suburban rail: unmodeled -> skipped
)
_TRIPS = (
    "trip_id,route_id,service_id,direction_id,shape_id\n"
    "T_TRAM,R_TRAM,WD,0,S1\n"
    "T_BUS,R_BUS,WD,0,S2\n"
    "T_M2,M2,WD,0,S3\n"
    "T_HEV,R_HEV,WD,0,S4\n"
)
_STOPS = (
    "stop_id,stop_name,stop_lat,stop_lon\n"
    "STA,Alpha,47.5000,19.0500\n"
    "STB,Bravo,47.5010,19.0510\n"
    "STC,Charlie,47.5020,19.0520\n"
    "STD,Delta,47.5030,19.0530\n"
    "STE,Echo,47.5040,19.0540\n"
    "STF,Foxtrot,47.7000,19.4000\n"   # ~25 km away
)
_STOP_TIMES = (
    "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
    "T_TRAM,STA,08:00:00,08:00:00,1\n"
    "T_TRAM,STB,08:03:00,08:03:00,2\n"
    "T_TRAM,STC,08:06:00,08:06:00,3\n"
    "T_BUS,STB,09:00:00,09:00:00,1\n"
    "T_BUS,STD,09:05:00,09:05:00,2\n"
    "T_M2,STA,07:00:00,07:00:00,1\n"
    "T_M2,STE,07:02:00,07:02:00,2\n"
)
_CALENDAR = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\n"
    "WD,1,1,1,1,1,0,0,20240101,20241231\n"
)


def _build_zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


@pytest.fixture()
def repo() -> GtfsRepository:
    members = {
        "routes.txt": _ROUTES,
        "trips.txt": _TRIPS,
        "stops.txt": _STOPS,
        "stop_times.txt": _STOP_TIMES,
        "calendar.txt": _CALENDAR,
    }
    tables = GtfsLoader().load_bytes(_build_zip(members), source="<fixture>")
    return GtfsRepository(tables)


# --- haversine sanity --------------------------------------------------------

def test_haversine_zero_distance():
    assert haversine_m(47.5, 19.05, 47.5, 19.05) == pytest.approx(0.0, abs=1e-6)


def test_haversine_known_distance():
    # ~1 degree of latitude is ~111 km.
    d = haversine_m(47.0, 19.0, 48.0, 19.0)
    assert d == pytest.approx(111_000, rel=0.01)


# --- list_lines + mode/color mapping (R2.2) ---------------------------------

def test_list_lines_skips_unmodeled_route_types(repo):
    ids = {line.route_id for line in repo.list_lines()}
    assert ids == {"R_TRAM", "R_BUS", "M2"}  # R_HEV (109) excluded


def test_list_lines_mode_and_color(repo):
    by_id = {line.route_id: line for line in repo.list_lines()}

    assert by_id["R_BUS"].mode is Mode.BUS
    assert by_id["R_BUS"].color == colors.BUS_COLOR

    assert by_id["R_TRAM"].mode is Mode.TRAM
    assert by_id["R_TRAM"].color == colors.TRAM_COLOR

    # Metro uses its official route_color from the feed.
    assert by_id["M2"].mode is Mode.METRO
    assert by_id["M2"].color == "#E41F18"


def test_mode_of_and_color_of(repo):
    assert repo.mode_of("R_BUS") is Mode.BUS
    assert repo.mode_of("R_TRAM") is Mode.TRAM
    assert repo.mode_of("M2") is Mode.METRO
    assert repo.mode_of("R_HEV") is None          # unmodeled
    assert repo.mode_of("nope") is None            # unknown

    assert repo.color_of("R_BUS") == colors.BUS_COLOR
    assert repo.color_of("M2") == "#E41F18"
    assert repo.color_of("nope") is None


# --- stops_for_line scoping (R4.2, R4.3) ------------------------------------

def test_stops_for_line_returns_only_that_lines_stops(repo):
    tram_ids = [s.stop_id for s in repo.stops_for_line("R_TRAM")]
    bus_ids = [s.stop_id for s in repo.stops_for_line("R_BUS")]

    assert tram_ids == ["STA", "STB", "STC"]  # in stop_sequence order
    assert bus_ids == ["STB", "STD"]

    # Scoping: the bus line must NOT leak tram-only stops, and vice versa.
    assert "STD" not in tram_ids
    assert "STA" not in bus_ids
    assert "STC" not in bus_ids


def test_stops_for_line_shared_stop_appears_in_both(repo):
    # STB is served by both lines; each line's scope includes it.
    assert "STB" in [s.stop_id for s in repo.stops_for_line("R_TRAM")]
    assert "STB" in [s.stop_id for s in repo.stops_for_line("R_BUS")]


def test_stops_for_line_unknown_route_is_empty(repo):
    assert repo.stops_for_line("does-not-exist") == []


# --- stops_near haversine radius (R2.1) -------------------------------------

def test_stops_near_includes_close_excludes_far(repo):
    # Query near STA/STB cluster with a 2 km radius: the far STF is excluded.
    near = repo.stops_near(47.5005, 19.0505, radius_m=2000)
    ids = {s.stop_id for s in near}
    assert "STA" in ids and "STB" in ids
    assert "STF" not in ids


def test_stops_near_sorted_nearest_first(repo):
    near = repo.stops_near(47.5000, 19.0500, radius_m=5000)
    # STA is exactly at the query point -> must be first.
    assert near[0].stop_id == "STA"


def test_stops_near_nonpositive_radius_is_empty(repo):
    assert repo.stops_near(47.5, 19.05, radius_m=0) == []
    assert repo.stops_near(47.5, 19.05, radius_m=-100) == []


# --- route_shape (R2.4) ------------------------------------------------------

def test_route_shape_matches_ordered_stops(repo):
    shape = repo.route_shape("R_TRAM")
    assert shape == [(47.5000, 19.0500), (47.5010, 19.0510), (47.5020, 19.0520)]


def test_route_shape_unknown_route_is_empty(repo):
    assert repo.route_shape("nope") == []


# --- scheduled_stop_times ----------------------------------------------------

def test_scheduled_stop_times_sorted_and_scoped(repo):
    df = repo.scheduled_stop_times("T_TRAM")
    assert list(df["stop_id"]) == ["STA", "STB", "STC"]
    assert list(df["stop_sequence"]) == [1, 2, 3]


def test_scheduled_stop_times_unknown_trip_is_empty(repo):
    assert repo.scheduled_stop_times("nope").empty


# --- mock fallback (R9.3, R10.3) --------------------------------------------

def test_from_zip_falls_back_to_mock_when_missing(tmp_path):
    repo = GtfsRepository.from_zip(tmp_path / "missing.zip")
    assert repo.is_mock is True
    # The mock catalog still answers queries so the UI has content.
    assert len(repo.list_lines()) > 0


def test_mock_repo_scopes_stops_per_line():
    repo = GtfsRepository.mock()
    lines = repo.list_lines()
    route_id = lines[0].route_id
    scoped = repo.stops_for_line(route_id)
    assert scoped == repo._mock.stops_for_line(route_id)  # only that line's stops


def test_mock_repo_mode_and_color_consistent():
    repo = GtfsRepository.mock()
    for line in repo.list_lines():
        assert repo.mode_of(line.route_id) is line.mode
        assert repo.color_of(line.route_id) == line.color


def test_live_repo_reports_not_mock(repo):
    assert repo.is_mock is False
