"""Unit tests for :mod:`backend.departure_service` (Requirements 5.1, 5.2).

The service merges the static schedule (via ``GtfsRepository``) with realtime
delays (via ``RealtimeSnapshot``) to produce upcoming departures for a stop.
The invariant under test is ``expected = scheduled + delay``.

Coverage:
* scheduled times are parsed against a service-date anchor and returned in
  order, filtered to at-or-after ``now``;
* a realtime delay produces ``expected = scheduled + delay`` and delay_min in
  minutes; delays match by (trip_id, stop_id) and by (trip_id, stop_sequence);
* no realtime snapshot -> every delay is 0 and expected == scheduled;
* only the selected stop's departures are returned (scoping);
* mock fallback when the repo is mock-backed or the schedule is empty;
* GTFS clock strings past 24:00:00 roll into the next calendar day.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from backend.departure_service import DepartureService, parse_gtfs_time
from backend.gtfs_loader import GtfsLoader
from backend.gtfs_repository import GtfsRepository
from backend.realtime_service import RealtimeSnapshot, StopTimeDelay


# --- Fixture GTFS feed -------------------------------------------------------
#
# Line R_TRAM has TWO trips both stopping at STB (so STB has two departures):
#   T_TRAM_1: STA 08:00 -> STB 08:03 -> STC 08:06
#   T_TRAM_2: STA 08:20 -> STB 08:23 -> STC 08:26
# Line R_BUS: STB 09:00 -> STD 09:05  (shares STB, used for scoping checks)
# A late-night trip on R_TRAM exercises the >24h rollover:
#   T_TRAM_N: STA 24:50 -> STB 24:53

_ROUTES = (
    "route_id,route_short_name,route_type,route_color\n"
    "R_TRAM,4,0,FFD800\n"
    "R_BUS,7,3,009EE3\n"
)
_TRIPS = (
    "trip_id,route_id,service_id,direction_id,shape_id\n"
    "T_TRAM_1,R_TRAM,WD,0,S1\n"
    "T_TRAM_2,R_TRAM,WD,0,S1\n"
    "T_TRAM_N,R_TRAM,WD,0,S1\n"
    "T_BUS,R_BUS,WD,0,S2\n"
)
_STOPS = (
    "stop_id,stop_name,stop_lat,stop_lon\n"
    "STA,Alpha,47.5000,19.0500\n"
    "STB,Bravo,47.5010,19.0510\n"
    "STC,Charlie,47.5020,19.0520\n"
    "STD,Delta,47.5030,19.0530\n"
)
_STOP_TIMES = (
    "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
    "T_TRAM_1,STA,08:00:00,08:00:00,1\n"
    "T_TRAM_1,STB,08:03:00,08:03:00,2\n"
    "T_TRAM_1,STC,08:06:00,08:06:00,3\n"
    "T_TRAM_2,STA,08:20:00,08:20:00,1\n"
    "T_TRAM_2,STB,08:23:00,08:23:00,2\n"
    "T_TRAM_2,STC,08:26:00,08:26:00,3\n"
    "T_TRAM_N,STA,24:50:00,24:50:00,1\n"
    "T_TRAM_N,STB,24:53:00,24:53:00,2\n"
    "T_BUS,STB,09:00:00,09:00:00,1\n"
    "T_BUS,STD,09:05:00,09:05:00,2\n"
)
_CALENDAR = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\n"
    "WD,1,1,1,1,1,0,0,20240101,20241231\n"
)

# A service-date anchor before all fixture departures so nothing is filtered
# out by the "at or after now" rule (except where a test sets now explicitly).
SERVICE_DATE = datetime(2024, 6, 3, 0, 0, 0, tzinfo=timezone.utc)  # a Monday

_BUDAPEST = ZoneInfo("Europe/Budapest")


def _bud(h, mi, *, day=3):
    """A Budapest-local wall-clock instant on 2024-06-``day`` (GTFS times are
    Budapest local, so the service anchors scheduled times in that zone)."""
    return datetime(2024, 6, day, h, mi, 0, tzinfo=_BUDAPEST)


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


@pytest.fixture()
def service(repo) -> DepartureService:
    return DepartureService(repo)


# --- parse_gtfs_time ---------------------------------------------------------


def test_parse_gtfs_time_basic():
    dt = parse_gtfs_time("08:03:00", SERVICE_DATE)
    assert dt == datetime(2024, 6, 3, 8, 3, 0, tzinfo=timezone.utc)


def test_parse_gtfs_time_rolls_past_midnight():
    # 24:53 belongs to the service day but lands on the next calendar day.
    dt = parse_gtfs_time("24:53:00", SERVICE_DATE)
    assert dt == datetime(2024, 6, 4, 0, 53, 0, tzinfo=timezone.utc)


def test_parse_gtfs_time_blank_or_malformed_returns_none():
    assert parse_gtfs_time("", SERVICE_DATE) is None
    assert parse_gtfs_time(None, SERVICE_DATE) is None
    assert parse_gtfs_time("nonsense", SERVICE_DATE) is None
    assert parse_gtfs_time("08:03", SERVICE_DATE) is None


# --- scheduled-only (no realtime) -> expected == scheduled -------------------


def test_upcoming_without_realtime_expected_equals_scheduled(service):
    deps = service.upcoming("R_TRAM", "STB", now=SERVICE_DATE)

    # Two tram trips stop at STB, in schedule order.
    assert [d.trip_id for d in deps] == ["T_TRAM_1", "T_TRAM_2", "T_TRAM_N"]
    for d in deps:
        assert d.delay_min == 0.0
        assert d.expected == d.scheduled

    assert deps[0].scheduled == _bud(8, 3)
    assert deps[1].scheduled == _bud(8, 23)
    # Late-night trip rolled into the next calendar day.
    assert deps[2].scheduled == _bud(0, 53, day=4)


# --- merge with realtime delay: expected = scheduled + delay -----------------


def test_upcoming_applies_realtime_delay_by_stop_id(service):
    snapshot = RealtimeSnapshot(
        fetched_at=SERVICE_DATE,
        stop_time_delays=[
            StopTimeDelay(
                trip_id="T_TRAM_1",
                route_id="R_TRAM",
                stop_id="STB",
                stop_sequence=2,
                delay_seconds=120,  # 2 minutes late
            )
        ],
    )

    deps = service.upcoming("R_TRAM", "STB", now=SERVICE_DATE, snapshot=snapshot)
    first = next(d for d in deps if d.trip_id == "T_TRAM_1")

    assert first.delay_min == 2.0
    # expected = scheduled + delay
    assert first.expected == _bud(8, 5)
    assert (first.expected - first.scheduled).total_seconds() == 120

    # The un-delayed trip is unaffected.
    second = next(d for d in deps if d.trip_id == "T_TRAM_2")
    assert second.delay_min == 0.0
    assert second.expected == second.scheduled


def test_upcoming_matches_delay_by_stop_sequence_when_stop_id_absent(service):
    # Feed omits stop_id but carries stop_sequence -> should still match.
    snapshot = RealtimeSnapshot(
        fetched_at=SERVICE_DATE,
        stop_time_delays=[
            StopTimeDelay(
                trip_id="T_TRAM_2",
                route_id="R_TRAM",
                stop_id="",
                stop_sequence=2,
                delay_seconds=90,
            )
        ],
    )

    deps = service.upcoming("R_TRAM", "STB", now=SERVICE_DATE, snapshot=snapshot)
    trip2 = next(d for d in deps if d.trip_id == "T_TRAM_2")
    assert trip2.delay_min == 1.5
    assert (trip2.expected - trip2.scheduled).total_seconds() == 90


def test_upcoming_negative_delay_makes_expected_earlier(service):
    snapshot = RealtimeSnapshot(
        fetched_at=SERVICE_DATE,
        stop_time_delays=[
            StopTimeDelay(
                trip_id="T_TRAM_1",
                route_id="R_TRAM",
                stop_id="STB",
                stop_sequence=2,
                delay_seconds=-60,  # running early
            )
        ],
    )
    deps = service.upcoming("R_TRAM", "STB", now=SERVICE_DATE, snapshot=snapshot)
    first = next(d for d in deps if d.trip_id == "T_TRAM_1")
    assert first.delay_min == -1.0
    assert first.expected == _bud(8, 2)


# --- scoping to the selected stop -------------------------------------------


def test_upcoming_scoped_to_selected_stop(service):
    # STA appears on the tram trips but must not surface when STB is requested.
    deps = service.upcoming("R_TRAM", "STB", now=SERVICE_DATE)
    times = {d.scheduled.strftime("%H:%M") for d in deps}
    assert "08:00" not in times  # that is STA's time
    assert "08:03" in times and "08:23" in times


def test_upcoming_only_returns_requested_route(service):
    # STB is also served by R_BUS at 09:00, but a tram query must not include it.
    deps = service.upcoming("R_TRAM", "STB", now=SERVICE_DATE)
    assert all(d.trip_id.startswith("T_TRAM") for d in deps)


# --- now-filtering and limit -------------------------------------------------


def test_upcoming_includes_whole_service_day(service):
    # R5.2/R5.4: all of the active service day's departures are returned,
    # including ones already in the past. now = 08:10 Budapest -> the 08:03
    # tram is in the past but must still be listed alongside the 08:23 one.
    now = _bud(8, 10)
    deps = service.upcoming("R_TRAM", "STB", now=now)
    trip_ids = [d.trip_id for d in deps]
    assert "T_TRAM_1" in trip_ids  # past departure still present
    assert "T_TRAM_2" in trip_ids  # upcoming departure present
    # R5.3: deterministic ordering by (scheduled, trip_id).
    keys = [(d.scheduled, d.trip_id) for d in deps]
    assert keys == sorted(keys)


def test_upcoming_future_only_mode_drops_past(service):
    # Opt-in future-only mode still filters out already-departed trips.
    now = _bud(8, 10)
    deps = service.upcoming("R_TRAM", "STB", now=now, include_past=False)
    trip_ids = [d.trip_id for d in deps]
    assert "T_TRAM_1" not in trip_ids
    assert "T_TRAM_2" in trip_ids


def test_upcoming_respects_limit(service):
    deps = service.upcoming("R_TRAM", "STB", limit=1, now=SERVICE_DATE)
    assert len(deps) == 1
    assert deps[0].trip_id == "T_TRAM_1"  # earliest


def test_upcoming_nonpositive_limit_is_empty(service):
    assert service.upcoming("R_TRAM", "STB", limit=0, now=SERVICE_DATE) == []


# --- fallback paths ----------------------------------------------------------


def test_upcoming_unknown_stop_falls_back_to_mock(service):
    # Unknown stop -> no schedule rows -> mock fallback (empty for unknown stop).
    deps = service.upcoming("R_TRAM", "NOPE", now=SERVICE_DATE)
    assert deps == []


def test_upcoming_mock_repo_uses_mock_departures():
    mock_repo = GtfsRepository.mock()
    svc = DepartureService(mock_repo)
    line = mock_repo.list_lines()[0]
    stop = mock_repo.stops_for_line(line.route_id)[0]

    deps = svc.upcoming(line.route_id, stop.stop_id, limit=5, now=SERVICE_DATE)
    assert len(deps) == 5
    # The mock invariant also holds: expected = scheduled + delay.
    for d in deps:
        assert d.expected == d.scheduled + __import__("datetime").timedelta(
            minutes=d.delay_min
        )


# --- regression: calendar filtering + per-trip dedupe (bug: same-minute dupes)


_ROUTES_CAL = "route_id,route_short_name,route_type,route_color\nR_M,M2,1,E41F18\n"
# Two trips at the SAME stop/time but on different service_ids (weekday vs
# weekend). Only the weekday one runs on the queried date, so the stop must
# show a single departure, not two identical ones.
_TRIPS_CAL = (
    "trip_id,route_id,service_id,direction_id,shape_id\n"
    "WK,R_M,WEEKDAY,0,S1\n"
    "WE,R_M,WEEKEND,0,S1\n"
)
_STOPS_CAL = (
    "stop_id,stop_name,stop_lat,stop_lon\n"
    "P1,First,47.50,19.05\n"
    "P2,Second,47.51,19.06\n"
)
_STOP_TIMES_CAL = (
    "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
    "WK,P1,08:00:00,08:00:00,1\n"
    "WK,P2,08:02:00,08:02:00,2\n"
    "WE,P1,08:00:00,08:00:00,1\n"
    "WE,P2,08:02:00,08:02:00,2\n"
)
# calendar_dates.txt form: only WEEKDAY runs on 2024-06-03 (a Monday).
_CALENDAR_DATES = (
    "service_id,date,exception_type\n"
    "WEEKDAY,20240603,1\n"
    "WEEKEND,20240601,1\n"
)


@pytest.fixture()
def cal_repo() -> GtfsRepository:
    members = {
        "routes.txt": _ROUTES_CAL,
        "trips.txt": _TRIPS_CAL,
        "stops.txt": _STOPS_CAL,
        "stop_times.txt": _STOP_TIMES_CAL,
        "calendar_dates.txt": _CALENDAR_DATES,
    }
    tables = GtfsLoader().load_bytes(_build_zip(members), source="<cal-fixture>")
    return GtfsRepository(tables)


def test_departures_filtered_to_active_service_day(cal_repo):
    """Only trips whose service runs on the queried date appear.

    Two trips share the same 08:00 slot at P1 on different service_ids; querying
    a weekday must yield exactly one departure (the weekday trip), not a
    same-minute duplicate from the weekend service.
    """
    svc = DepartureService(cal_repo)
    deps = svc.upcoming("R_M", "P1", now=SERVICE_DATE)  # 2024-06-03, Monday
    assert len(deps) == 1
    assert deps[0].trip_id == "WK"
    assert deps[0].scheduled == _bud(8, 0)
