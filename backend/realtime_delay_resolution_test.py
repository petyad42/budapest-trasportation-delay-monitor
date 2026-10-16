"""Tests for deriving GTFS-RT delays from absolute predicted times.

Some feeds (notably BKK) omit the GTFS-RT ``delay`` field and instead report an
absolute predicted ``time`` (Unix epoch). ``parse_trip_updates`` captures that
as ``predicted_time`` and ``resolve_delays_against_schedule`` derives
``delay_seconds = predicted_time - scheduled_time`` using the static schedule.

These tests pin down:
* the explicit-``delay`` path is unchanged (delay read directly, no predicted_time);
* the time-only path yields ``predicted_time`` and ``delay_seconds is None`` before resolution;
* resolution derives the correct signed delay, timezone-correct (Budapest local
  schedule vs. UTC epoch);
* matching works by stop_sequence and falls back to stop_id;
* mock-backed repositories and already-resolved observations are left untouched.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from google.transit import gtfs_realtime_pb2

from backend.gtfs_loader import GtfsLoader
from backend.gtfs_repository import GtfsRepository
from backend.realtime_service import (
    parse_trip_updates,
    resolve_delays_against_schedule,
)

_BUDAPEST = ZoneInfo("Europe/Budapest")


# --- fixtures ---------------------------------------------------------------
_ROUTES = "route_id,route_short_name,route_type,route_color\nR1,7,3,009EE3\n"
_TRIPS = "trip_id,route_id,service_id,direction_id,shape_id\nT1,R1,WD,0,S1\n"
_STOPS = (
    "stop_id,stop_name,stop_lat,stop_lon\n"
    "STA,Alpha,47.5000,19.0500\n"
    "STB,Bravo,47.5010,19.0510\n"
)
_STOP_TIMES = (
    "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
    "T1,STA,08:00:00,08:00:00,1\n"
    "T1,STB,08:10:00,08:10:00,2\n"
)
_CALENDAR = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\nWD,1,1,1,1,1,0,0,20240101,20241231\n"
)


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("routes.txt", _ROUTES)
        zf.writestr("trips.txt", _TRIPS)
        zf.writestr("stops.txt", _STOPS)
        zf.writestr("stop_times.txt", _STOP_TIMES)
        zf.writestr("calendar.txt", _CALENDAR)
    return buf.getvalue()


def _repo() -> GtfsRepository:
    tables = GtfsLoader().load_bytes(_zip_bytes(), source="<fixture>")
    return GtfsRepository(tables)


def _budapest_epoch(day: datetime, clock: str) -> int:
    """Epoch seconds for ``clock`` (HH:MM:SS) on ``day`` in Budapest local time."""
    h, m, sec = (int(p) for p in clock.split(":"))
    local = day.astimezone(_BUDAPEST).replace(
        hour=h, minute=m, second=sec, microsecond=0
    )
    return int(local.timestamp())


def _time_only_pb(trip_id, route_id, stop_id, seq, predicted_epoch) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    entity = feed.entity.add()
    entity.id = "e0"
    tu = entity.trip_update
    tu.trip.trip_id = trip_id
    tu.trip.route_id = route_id
    stu = tu.stop_time_update.add()
    stu.stop_id = stop_id
    stu.stop_sequence = seq
    stu.arrival.time = predicted_epoch  # absolute time, NO delay field
    return feed.SerializeToString()


# --- explicit-delay path unchanged -----------------------------------------
def test_explicit_delay_field_still_read_directly():
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "e0"
    e.trip_update.trip.trip_id = "T1"
    e.trip_update.trip.route_id = "R1"
    stu = e.trip_update.stop_time_update.add()
    stu.stop_id = "STA"
    stu.stop_sequence = 1
    stu.arrival.delay = 90

    snap = parse_trip_updates(feed.SerializeToString())
    obs = snap.stop_time_delays[0]
    assert obs.delay_seconds == 90
    assert obs.predicted_time is None


# --- time-only path: captured, then resolved --------------------------------
def test_time_only_observation_has_none_delay_before_resolution():
    day = datetime(2024, 6, 3, 6, 5, tzinfo=timezone.utc)
    predicted = _budapest_epoch(day, "08:03:00")  # 3 min late vs 08:00 schedule
    snap = parse_trip_updates(_time_only_pb("T1", "R1", "STA", 1, predicted))
    obs = snap.stop_time_delays[0]
    assert obs.delay_seconds is None
    assert obs.predicted_time == predicted


def test_resolution_derives_signed_delay_timezone_correct():
    day = datetime(2024, 6, 3, 6, 5, tzinfo=timezone.utc)
    predicted = _budapest_epoch(day, "08:03:00")  # scheduled 08:00 local -> +180s
    snap = parse_trip_updates(_time_only_pb("T1", "R1", "STA", 1, predicted))

    resolved = resolve_delays_against_schedule(snap, _repo(), service_date=day)
    obs = resolved.stop_time_delays[0]
    assert obs.delay_seconds == 180  # exactly 3 minutes late, not off by tz offset


def test_resolution_handles_early_arrival_as_negative_delay():
    day = datetime(2024, 6, 3, 6, 5, tzinfo=timezone.utc)
    predicted = _budapest_epoch(day, "07:58:30")  # 90s early vs 08:00
    snap = parse_trip_updates(_time_only_pb("T1", "R1", "STA", 1, predicted))
    resolved = resolve_delays_against_schedule(snap, _repo(), service_date=day)
    assert resolved.stop_time_delays[0].delay_seconds == -90


def test_resolution_matches_by_stop_id_when_sequence_absent():
    day = datetime(2024, 6, 3, 6, 5, tzinfo=timezone.utc)
    predicted = _budapest_epoch(day, "08:12:00")  # +120s vs STB's 08:10
    # Build a feed with NO stop_sequence, only stop_id.
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "e0"
    e.trip_update.trip.trip_id = "T1"
    e.trip_update.trip.route_id = "R1"
    stu = e.trip_update.stop_time_update.add()
    stu.stop_id = "STB"
    stu.arrival.time = predicted
    snap = parse_trip_updates(feed.SerializeToString())
    assert snap.stop_time_delays[0].stop_sequence is None

    resolved = resolve_delays_against_schedule(snap, _repo(), service_date=day)
    assert resolved.stop_time_delays[0].delay_seconds == 120


def test_resolution_falls_back_to_route_stop_for_unknown_trip():
    """BKK reissues trip_ids per schedule version, so a live trip often has no
    static match. The resolver then derives the delay from the nearest static
    (route, stop) scheduled time instead of giving up (-> None)."""
    day = datetime(2024, 6, 3, 6, 5, tzinfo=timezone.utc)
    predicted = _budapest_epoch(day, "08:03:00")  # 3 min after STA's 08:00
    snap = parse_trip_updates(_time_only_pb("UNKNOWN", "R1", "STA", 1, predicted))
    resolved = resolve_delays_against_schedule(snap, _repo(), service_date=day)
    assert resolved.stop_time_delays[0].delay_seconds == 180


def test_resolution_fallback_requires_known_route_and_stop():
    """The route/stop fallback still can't invent a delay for a stop/route that
    isn't in the static schedule at all -> delay stays None."""
    day = datetime(2024, 6, 3, 6, 5, tzinfo=timezone.utc)
    predicted = _budapest_epoch(day, "08:03:00")
    # Route R1 exists but stop ZZZ is not on it.
    snap = parse_trip_updates(_time_only_pb("UNKNOWN", "R1", "ZZZ", 1, predicted))
    resolved = resolve_delays_against_schedule(snap, _repo(), service_date=day)
    assert resolved.stop_time_delays[0].delay_seconds is None


def test_resolution_fallback_rejects_out_of_window_prediction():
    """A prediction more than a few hours from any scheduled time at that stop
    must not be snapped to the wrong run of the day -> delay stays None."""
    day = datetime(2024, 6, 3, 6, 5, tzinfo=timezone.utc)
    # STA is scheduled at 08:00 only; predict ~6h later -> beyond the 3h window.
    predicted = _budapest_epoch(day, "14:00:00")
    snap = parse_trip_updates(_time_only_pb("UNKNOWN", "R1", "STA", 1, predicted))
    resolved = resolve_delays_against_schedule(snap, _repo(), service_date=day)
    assert resolved.stop_time_delays[0].delay_seconds is None


def test_resolution_noop_on_mock_repo():
    day = datetime(2024, 6, 3, 6, 5, tzinfo=timezone.utc)
    predicted = _budapest_epoch(day, "08:03:00")
    snap = parse_trip_updates(_time_only_pb("T1", "R1", "STA", 1, predicted))
    mock_repo = GtfsRepository.mock()
    resolved = resolve_delays_against_schedule(snap, mock_repo, service_date=day)
    # Mock has no schedule -> unchanged (delay stays None).
    assert resolved.stop_time_delays[0].delay_seconds is None


def test_resolution_preserves_already_resolved_observations():
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    e = feed.entity.add()
    e.id = "e0"
    e.trip_update.trip.trip_id = "T1"
    e.trip_update.trip.route_id = "R1"
    stu = e.trip_update.stop_time_update.add()
    stu.stop_id = "STA"
    stu.stop_sequence = 1
    stu.arrival.delay = 42
    snap = parse_trip_updates(feed.SerializeToString())
    day = datetime(2024, 6, 3, 6, 5, tzinfo=timezone.utc)
    resolved = resolve_delays_against_schedule(snap, _repo(), service_date=day)
    assert resolved.stop_time_delays[0].delay_seconds == 42
