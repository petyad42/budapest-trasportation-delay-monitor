"""Unit tests for the GTFS-Realtime RealtimeService.

No live network is used: outbound HTTP is mocked with a fake ``httpx.Client``,
and the GTFS-RT payload is a *real* protobuf built in-process with
``gtfs-realtime-bindings`` so the parser is exercised end-to-end. The clock is
injected so staleness is deterministic.

Coverage:
* successful poll parses TripUpdates and caches a fresh snapshot -> status LIVE
  (Requirements 1.2, 9.2);
* status flips to MOCK when the poll fails (Requirements 1.3, 9.3);
* status flips to MOCK when the cached snapshot is stale (Requirements 1.3,
  9.3, 10.2);
* status is MOCK before the first poll.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from google.transit import gtfs_realtime_pb2

from backend.realtime_service import (
    RealtimeService,
    RealtimeSnapshot,
    StopTimeDelay,
    parse_trip_updates,
)


# --- helpers ---------------------------------------------------------------


def _build_trip_updates_pb(
    updates=(("trip-1", "route-7", "stop-A", 1, 120),),
    feed_timestamp=1_700_000_000,
) -> bytes:
    """Build a real GTFS-RT TripUpdates protobuf payload.

    ``updates`` is an iterable of
    ``(trip_id, route_id, stop_id, stop_sequence, arrival_delay_seconds)``.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    if feed_timestamp is not None:
        feed.header.timestamp = feed_timestamp

    for i, (trip_id, route_id, stop_id, seq, delay) in enumerate(updates):
        entity = feed.entity.add()
        entity.id = f"e{i}"
        tu = entity.trip_update
        tu.trip.trip_id = trip_id
        tu.trip.route_id = route_id
        stu = tu.stop_time_update.add()
        stu.stop_id = stop_id
        stu.stop_sequence = seq
        stu.arrival.delay = delay

    return feed.SerializeToString()


class _FakeResponse:
    def __init__(self, content=b"", raise_exc=None):
        self.content = content
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc


class _FakeClient:
    """Stand-in ``httpx.Client`` that returns fixed bytes or raises."""

    def __init__(self, content=b"", raise_exc=None):
        self._content = content
        self._raise_exc = raise_exc
        self.calls = 0
        self.last_params = None

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        self.last_params = params
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeResponse(content=self._content)


class _FrozenClock:
    """Mutable clock so tests can advance time deterministically."""

    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


# --- parsing ---------------------------------------------------------------


def test_parse_trip_updates_extracts_arrival_delay():
    raw = _build_trip_updates_pb(
        updates=(("trip-1", "route-7", "stop-A", 3, 90),)
    )

    snapshot = parse_trip_updates(raw)

    assert isinstance(snapshot, RealtimeSnapshot)
    assert snapshot.feed_timestamp == 1_700_000_000
    assert len(snapshot.stop_time_delays) == 1
    delay = snapshot.stop_time_delays[0]
    assert delay == StopTimeDelay(
        trip_id="trip-1",
        route_id="route-7",
        stop_id="stop-A",
        stop_sequence=3,
        delay_seconds=90,
    )


def test_parse_trip_updates_falls_back_to_departure_delay():
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    entity = feed.entity.add()
    entity.id = "e0"
    tu = entity.trip_update
    tu.trip.trip_id = "trip-x"
    tu.trip.route_id = "route-9"
    stu = tu.stop_time_update.add()
    stu.stop_id = "stop-Z"
    stu.departure.delay = 45  # no arrival set

    snapshot = parse_trip_updates(feed.SerializeToString())

    assert snapshot.stop_time_delays[0].delay_seconds == 45


def test_parse_trip_updates_empty_feed():
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"

    snapshot = parse_trip_updates(feed.SerializeToString())

    assert snapshot.stop_time_delays == []
    assert isinstance(snapshot.fetched_at, datetime)


# --- successful poll -> LIVE ------------------------------------------------


def test_poll_once_success_caches_snapshot_and_status_is_live():
    clock = _FrozenClock(datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc))
    client = _FakeClient(content=_build_trip_updates_pb())
    service = RealtimeService(
        client=client,
        clock=clock,
        staleness_threshold_seconds=60,
        api_key="test-key",
    )

    ok = service.poll_once()

    assert ok is True
    assert client.calls == 1
    # API key is forwarded as a query param (free BKK key, R9.5).
    assert client.last_params == {"key": "test-key"}

    snapshot = service.latest_trip_updates()
    assert snapshot is not None
    assert len(snapshot.stop_time_delays) == 1

    status = service.status
    assert status.source == "realtime"
    assert status.stale is False


# --- poll failure -> MOCK ---------------------------------------------------


def test_status_is_mock_on_poll_failure():
    clock = _FrozenClock(datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc))
    client = _FakeClient(raise_exc=httpx.ConnectError("boom"))
    service = RealtimeService(client=client, clock=clock)

    ok = service.poll_once()

    assert ok is False
    # No snapshot was ever cached -> mock.
    assert service.status.source == "mock"


def test_status_flips_to_mock_when_later_poll_fails():
    clock = _FrozenClock(datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc))
    good_client = _FakeClient(content=_build_trip_updates_pb())
    service = RealtimeService(
        client=good_client, clock=clock, staleness_threshold_seconds=300
    )

    # First poll succeeds -> LIVE.
    assert service.poll_once() is True
    assert service.status.source == "realtime"

    # Now the network starts failing. Swap in a failing client and re-poll.
    service._client = _FakeClient(raise_exc=httpx.TimeoutException("slow"))
    assert service.poll_once() is False

    # Even though a (still-fresh) snapshot exists, the last poll failed, so the
    # service must honestly report mock (R1.3, R9.3, R10.2).
    assert service.status.source == "mock"


def test_poll_failure_does_not_raise():
    service = RealtimeService(client=_FakeClient(raise_exc=RuntimeError("nope")))
    # Must degrade, never propagate.
    assert service.poll_once() is False


# --- staleness -> MOCK ------------------------------------------------------


def test_status_flips_to_mock_on_staleness():
    clock = _FrozenClock(datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc))
    client = _FakeClient(content=_build_trip_updates_pb())
    service = RealtimeService(
        client=client, clock=clock, staleness_threshold_seconds=60
    )

    assert service.poll_once() is True
    assert service.status.source == "realtime"

    # Advance the clock past the staleness threshold without re-polling.
    clock.advance(61)

    status = service.status
    assert status.stale is True
    assert status.source == "mock"


def test_fresh_snapshot_within_threshold_stays_live():
    clock = _FrozenClock(datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc))
    client = _FakeClient(content=_build_trip_updates_pb())
    service = RealtimeService(
        client=client, clock=clock, staleness_threshold_seconds=60
    )

    assert service.poll_once() is True
    clock.advance(59)  # still within threshold

    status = service.status
    assert status.stale is False
    assert status.source == "realtime"


# --- before first poll ------------------------------------------------------


def test_status_is_mock_before_first_poll():
    service = RealtimeService(client=_FakeClient(content=b""))
    assert service.latest_trip_updates() is None
    status = service.status
    assert status.source == "mock"
    assert status.stale is True
