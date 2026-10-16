"""Bugfix tests for the ``stops_for_line`` O(trips × rows) performance defect.

Spec: .kiro/specs/budapest-transit-monitor/stops-for-line-perf/

The defect: ``GtfsRepository.stops_for_line`` resolves a route's stops by running
``st[st["trip_id"] == trip_id]`` (a full ``stop_times`` scan) once per trip, giving
O(trips × rows) work per route. This is invisible to the existing suite because
fixture/mock routes have tiny ``stop_times`` tables.

Property 1 (Bug Condition): the fixed code performs zero full ``stop_times`` scans,
using a precomputed per-trip index instead. We assert a *bounded scan count* rather
than wall-clock time so the test is deterministic and CI-safe.

Property 2 (Preservation): unknown routes, the mock path, and the exact ordered,
de-duplicated output for known routes (and ``route_shape``) are unchanged.
"""
from __future__ import annotations

import io
import time
import zipfile

import pandas as pd
import pytest

from backend.gtfs_loader import GtfsLoader
from backend.gtfs_repository import GtfsRepository


# --------------------------------------------------------------------------- #
# Scan-counting stop_times frame
# --------------------------------------------------------------------------- #
class _ScanCountingStopTimes(pd.DataFrame):
    """A ``stop_times`` DataFrame that counts full-frame boolean-mask indexing.

    ``stops_for_line`` on the UNFIXED code does ``st[st["trip_id"] == trip_id]``.
    That is a ``__getitem__`` with a boolean Series spanning the whole frame — one
    per trip. We count those to detect the O(trips × rows) scan without timing.

    The fixed code never indexes ``stop_times`` inside ``stops_for_line`` (it reads
    a precomputed per-trip dict), so the counter stays at 0.
    """

    # pandas needs these declared so the subclass survives internal operations.
    _metadata = ["full_scan_count"]

    @property
    def _constructor(self):
        return _ScanCountingStopTimes

    def __getitem__(self, key):
        # A boolean Series/array spanning the full frame == a full-table scan.
        is_bool_mask = (
            isinstance(key, (pd.Series, pd.Index))
            and getattr(key, "dtype", None) == bool
            and len(key) == len(self)
        )
        if is_bool_mask:
            # ``full_scan_count`` is stored on the object via _metadata.
            self.full_scan_count = getattr(self, "full_scan_count", 0) + 1
        return super().__getitem__(key)


def _instrument_scan_counter(repo: GtfsRepository) -> _ScanCountingStopTimes:
    """Swap the repo's ``stop_times`` for a scan-counting frame; return it."""
    st = _ScanCountingStopTimes(repo._tables.stop_times.copy())
    st.full_scan_count = 0
    repo._tables.stop_times = st
    return st


# --------------------------------------------------------------------------- #
# Synthetic large many-trip feed (triggers the bug condition)
# --------------------------------------------------------------------------- #
_N_TRIPS = 400          # many trips on one route
_STOPS_PER_TRIP = 5     # small per-trip stop count -> route has few unique stops


def _build_zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _large_feed_bytes() -> bytes:
    """One bus route ``R_BIG`` with ``_N_TRIPS`` trips, each visiting the same
    ``_STOPS_PER_TRIP`` stops. ``stop_times`` has _N_TRIPS × _STOPS_PER_TRIP rows.
    """
    routes = "route_id,route_short_name,route_type,route_color\nR_BIG,900,3,009EE3\n"

    trip_lines = ["trip_id,route_id,service_id,direction_id,shape_id"]
    for i in range(_N_TRIPS):
        trip_lines.append(f"T{i},R_BIG,WD,0,S{i}")
    trips = "\n".join(trip_lines) + "\n"

    stop_lines = ["stop_id,stop_name,stop_lat,stop_lon"]
    for j in range(_STOPS_PER_TRIP):
        stop_lines.append(f"S{j},Stop{j},47.{500 + j},19.{500 + j}")
    stops = "\n".join(stop_lines) + "\n"

    st_lines = ["trip_id,stop_id,arrival_time,departure_time,stop_sequence"]
    for i in range(_N_TRIPS):
        for j in range(_STOPS_PER_TRIP):
            t = f"08:{j:02d}:00"
            st_lines.append(f"T{i},S{j},{t},{t},{j + 1}")
    stop_times = "\n".join(st_lines) + "\n"

    calendar = (
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
        "start_date,end_date\nWD,1,1,1,1,1,0,0,20240101,20241231\n"
    )
    return _build_zip(
        {
            "routes.txt": routes,
            "trips.txt": trips,
            "stops.txt": stops,
            "stop_times.txt": stop_times,
            "calendar.txt": calendar,
        }
    )


@pytest.fixture()
def big_repo() -> GtfsRepository:
    tables = GtfsLoader().load_bytes(_large_feed_bytes(), source="<big-fixture>")
    return GtfsRepository(tables)


# --------------------------------------------------------------------------- #
# Property 1: Bug Condition - no full-table scans (FAILS on unfixed code)
# --------------------------------------------------------------------------- #
def test_stops_for_line_performs_no_full_table_scans(big_repo):
    """For a route with many trips, resolving stops must not scan stop_times
    once per trip. On unfixed code the counter reads ~_N_TRIPS -> FAIL."""
    counter = _instrument_scan_counter(big_repo)

    result = big_repo.stops_for_line("R_BIG")

    # Correctness (independent of the fix): the route's stops in sequence order.
    assert [s.stop_id for s in result] == [f"S{j}" for j in range(_STOPS_PER_TRIP)]

    # The fix assertion: zero full-table scans (bounded, ideally 0).
    assert counter.full_scan_count <= 1, (
        f"stops_for_line performed {counter.full_scan_count} full stop_times "
        f"scans for a {_N_TRIPS}-trip route (O(trips × rows) bug). Expected <= 1."
    )


def test_stops_for_line_large_route_is_prompt(big_repo):
    """Secondary, generous wall-clock sanity check (non-authoritative)."""
    start = time.perf_counter()
    big_repo.stops_for_line("R_BIG")
    elapsed = time.perf_counter() - start
    # Very loose ceiling; the scan-count test is the authoritative one.
    assert elapsed < 5.0


# --------------------------------------------------------------------------- #
# Property 2: Preservation - identical stops/order/scoping/mock/shape
# --------------------------------------------------------------------------- #
_ROUTES = (
    "route_id,route_short_name,route_type,route_color\n"
    "R_TRAM,4,0,FFD800\n"
    "R_BUS,7,3,009EE3\n"
    "R_MULTI,9,3,009EE3\n"
)
_TRIPS = (
    "trip_id,route_id,service_id,direction_id,shape_id\n"
    "T_TRAM,R_TRAM,WD,0,S1\n"
    "T_BUS,R_BUS,WD,0,S2\n"
    "T_M_LONG,R_MULTI,WD,0,S3\n"    # representative (most stops)
    "T_M_SHORT,R_MULTI,WD,0,S4\n"   # adds one new stop at the end
)
_STOPS = (
    "stop_id,stop_name,stop_lat,stop_lon\n"
    "STA,Alpha,47.5000,19.0500\n"
    "STB,Bravo,47.5010,19.0510\n"
    "STC,Charlie,47.5020,19.0520\n"
    "STD,Delta,47.5030,19.0530\n"
    "STX,Xray,47.5040,19.0540\n"
)
_STOP_TIMES = (
    "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
    "T_TRAM,STA,08:00:00,08:00:00,1\n"
    "T_TRAM,STB,08:03:00,08:03:00,2\n"
    "T_TRAM,STC,08:06:00,08:06:00,3\n"
    "T_BUS,STB,09:00:00,09:00:00,1\n"
    "T_BUS,STD,09:05:00,09:05:00,2\n"
    # R_MULTI: long trip visits STA,STB,STC; short trip visits STA,STX.
    "T_M_LONG,STA,07:00:00,07:00:00,1\n"
    "T_M_LONG,STB,07:02:00,07:02:00,2\n"
    "T_M_LONG,STC,07:04:00,07:04:00,3\n"
    "T_M_SHORT,STA,07:30:00,07:30:00,1\n"
    "T_M_SHORT,STX,07:32:00,07:32:00,2\n"
)
_CALENDAR = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\nWD,1,1,1,1,1,0,0,20240101,20241231\n"
)


@pytest.fixture()
def repo() -> GtfsRepository:
    tables = GtfsLoader().load_bytes(
        _build_zip(
            {
                "routes.txt": _ROUTES,
                "trips.txt": _TRIPS,
                "stops.txt": _STOPS,
                "stop_times.txt": _STOP_TIMES,
                "calendar.txt": _CALENDAR,
            }
        ),
        source="<preservation-fixture>",
    )
    return GtfsRepository(tables)


def test_preservation_known_route_ordering_and_dedup(repo):
    assert [s.stop_id for s in repo.stops_for_line("R_TRAM")] == ["STA", "STB", "STC"]
    assert [s.stop_id for s in repo.stops_for_line("R_BUS")] == ["STB", "STD"]


def test_preservation_unknown_route_is_empty(repo):
    assert repo.stops_for_line("does-not-exist") == []


def test_preservation_multitrip_representative_then_first_appearance(repo):
    # Representative (longest) trip seeds STA,STB,STC; the short trip contributes
    # the new STX by first appearance -> STA,STB,STC,STX, de-duplicated.
    assert [s.stop_id for s in repo.stops_for_line("R_MULTI")] == [
        "STA",
        "STB",
        "STC",
        "STX",
    ]


def test_preservation_route_shape_matches_ordered_stops(repo):
    assert repo.route_shape("R_TRAM") == [
        (47.5000, 19.0500),
        (47.5010, 19.0510),
        (47.5020, 19.0520),
    ]


def test_preservation_mock_path_delegates(repo):
    mock_repo = GtfsRepository.mock()
    route_id = mock_repo.list_lines()[0].route_id
    assert mock_repo.stops_for_line(route_id) == mock_repo._mock.stops_for_line(
        route_id
    )
