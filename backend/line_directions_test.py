"""Tests for direction-aware line stops + flip metadata (Requirement 4).

A line (``R_TRAM``) serves two directions:
  * direction 0: Alpha -> Bravo -> Charlie -> Delta
  * direction 1: Delta -> Charlie -> Bravo -> Alpha
A second line (``R_BUS``) serves a single direction, so its flip control must
be reported as unavailable (one direction only).
"""
from __future__ import annotations

import io
import zipfile

import pytest

from backend.gtfs_loader import GtfsLoader
from backend.gtfs_repository import GtfsRepository

_ROUTES = (
    "route_id,route_short_name,route_type,route_color\n"
    "R_TRAM,4,0,FFD800\n"
    "R_BUS,7,3,009EE3\n"
)
_TRIPS = (
    "trip_id,route_id,service_id,direction_id,shape_id\n"
    "T_TRAM_D0,R_TRAM,WD,0,S1\n"
    "T_TRAM_D1,R_TRAM,WD,1,S2\n"
    "T_BUS,R_BUS,WD,0,S3\n"
)
_STOPS = (
    "stop_id,stop_name,stop_lat,stop_lon\n"
    "STA,Alpha,47.5000,19.0500\n"
    "STB,Bravo,47.5010,19.0510\n"
    "STC,Charlie,47.5020,19.0520\n"
    "STD,Delta,47.5030,19.0530\n"
    "STX,Xray,47.6000,19.1000\n"
)
_STOP_TIMES = (
    "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
    # direction 0: Alpha -> Delta
    "T_TRAM_D0,STA,08:00:00,08:00:00,1\n"
    "T_TRAM_D0,STB,08:03:00,08:03:00,2\n"
    "T_TRAM_D0,STC,08:06:00,08:06:00,3\n"
    "T_TRAM_D0,STD,08:09:00,08:09:00,4\n"
    # direction 1: Delta -> Alpha (reverse)
    "T_TRAM_D1,STD,09:00:00,09:00:00,1\n"
    "T_TRAM_D1,STC,09:03:00,09:03:00,2\n"
    "T_TRAM_D1,STB,09:06:00,09:06:00,3\n"
    "T_TRAM_D1,STA,09:09:00,09:09:00,4\n"
    # single-direction bus; STX only lives on direction 0 here
    "T_BUS,STX,10:00:00,10:00:00,1\n"
    "T_BUS,STB,10:05:00,10:05:00,2\n"
)
_CALENDAR = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\n"
    "WD,1,1,1,1,1,0,0,20240101,20241231\n"
)


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


@pytest.fixture()
def repo() -> GtfsRepository:
    tables = GtfsLoader().load_bytes(
        _zip(
            {
                "routes.txt": _ROUTES,
                "trips.txt": _TRIPS,
                "stops.txt": _STOPS,
                "stop_times.txt": _STOP_TIMES,
                "calendar.txt": _CALENDAR,
            }
        ),
        source="<fixture>",
    )
    return GtfsRepository(tables)


def test_available_directions(repo):
    assert repo.available_directions("R_TRAM") == ["0", "1"]
    assert repo.available_directions("R_BUS") == ["0"]
    assert repo.available_directions("NOPE") == []


def test_direction_zero_ordered_start_to_end(repo):
    stops = repo.stops_for_line_direction("R_TRAM", "0")
    names = [s.name for s in stops]
    assert names == ["Alpha", "Bravo", "Charlie", "Delta"]


def test_direction_one_is_reverse_order(repo):
    stops = repo.stops_for_line_direction("R_TRAM", "1")
    names = [s.name for s in stops]
    assert names == ["Delta", "Charlie", "Bravo", "Alpha"]


def test_no_duplicate_stops_in_a_direction(repo):
    stops = repo.stops_for_line_direction("R_TRAM", "0")
    ids = [s.stop_id for s in stops]
    assert len(ids) == len(set(ids))


def test_line_directions_default_active_is_lowest(repo):
    ld = repo.line_directions("R_TRAM")
    assert ld is not None
    assert ld.active_direction == "0"
    assert [s.name for s in ld.stops] == ["Alpha", "Bravo", "Charlie", "Delta"]
    # Both directions exposed with their termini.
    by_id = {d.direction_id: d for d in ld.directions}
    assert by_id["0"].start_terminus == "Alpha"
    assert by_id["0"].end_terminus == "Delta"
    assert by_id["1"].start_terminus == "Delta"
    assert by_id["1"].end_terminus == "Alpha"


def test_line_directions_select_opposite(repo):
    ld = repo.line_directions("R_TRAM", "1")
    assert ld is not None
    assert ld.active_direction == "1"
    assert [s.name for s in ld.stops] == ["Delta", "Charlie", "Bravo", "Alpha"]


def test_line_directions_unknown_direction_falls_back_to_default(repo):
    ld = repo.line_directions("R_TRAM", "9")
    assert ld is not None
    assert ld.active_direction == "0"


def test_single_direction_line_has_one_direction(repo):
    ld = repo.line_directions("R_BUS")
    assert ld is not None
    assert len(ld.directions) == 1
    assert ld.active_direction == "0"


def test_line_directions_unknown_route_is_none(repo):
    assert repo.line_directions("NOPE") is None


def test_stop_preserved_across_flip_when_present(repo):
    # Bravo exists in both directions -> a flip can preserve it.
    d0 = {s.stop_id for s in repo.stops_for_line_direction("R_TRAM", "0")}
    d1 = {s.stop_id for s in repo.stops_for_line_direction("R_TRAM", "1")}
    assert "STB" in d0 and "STB" in d1
