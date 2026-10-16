"""GTFS-Realtime ingestion service with background polling and status.

:class:`RealtimeService` polls the BKK GTFS-Realtime *TripUpdates* protobuf feed
on a configurable interval, parses it with ``gtfs-realtime-bindings``, and caches
the latest snapshot together with the wall-clock time it was fetched
(Requirements 1.2, 9.2). Per-stop arrival/departure delays (in seconds) are
extracted from each ``stop_time_update`` so downstream services (DelayComputer,
DepartureService) can join them against the static schedule.

Design principle: **degrade, do not fail.** When a poll fails, or when the most
recent successful snapshot has aged past the staleness threshold, the service's
:pyattr:`status` flips from ``realtime`` to ``mock`` so the UI's data-status
indicator turns orange and callers substitute mock data (Requirements 1.3, 9.3,
10.2).

Testability: the poll loop, the HTTP client, and the clock are all injectable.
:meth:`poll_once` performs exactly one fetch/parse/cache cycle and is what the
unit tests exercise, so no live network or real timer is required.
"""
from __future__ import annotations

import dataclasses
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, List, Optional

import httpx
from google.transit import gtfs_realtime_pb2

from backend import config
from backend.models import DataStatus

logger = logging.getLogger(__name__)

# A callable returning "now" as an aware UTC datetime. Injectable for tests.
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StopTimeDelay:
    """A single per-stop delay observation from a TripUpdate.

    ``delay_seconds`` is the arrival delay when the feed reports an explicit
    GTFS-RT ``delay`` field (arrival preferred, else departure); ``None`` when
    the feed carried neither.

    Some feeds (notably BKK) omit ``delay`` and instead report an absolute
    predicted ``time`` (Unix epoch seconds). ``predicted_time`` captures that
    raw value (arrival preferred, else departure) so a later schedule join can
    derive ``delay_seconds = predicted_time - scheduled_time`` (see
    :func:`resolve_delays_against_schedule`).
    """

    trip_id: str
    route_id: str
    stop_id: str
    stop_sequence: Optional[int]
    delay_seconds: Optional[int]
    predicted_time: Optional[int] = None


@dataclass(frozen=True)
class RealtimeSnapshot:
    """The latest parsed GTFS-RT TripUpdates, plus when it was fetched.

    An *empty* snapshot (no updates) is still a valid, fresh snapshot; freshness
    is determined solely by ``fetched_at`` relative to the staleness threshold.
    """

    fetched_at: datetime
    feed_timestamp: Optional[int] = None
    stop_time_delays: List[StopTimeDelay] = field(default_factory=list)

    def is_stale(self, threshold_seconds: float, now: datetime) -> bool:
        """Return ``True`` when this snapshot has aged past ``threshold_seconds``."""
        age = (now - self.fetched_at).total_seconds()
        return age > threshold_seconds


def parse_trip_updates(raw: bytes) -> RealtimeSnapshot:
    """Parse a GTFS-RT TripUpdates protobuf payload into a snapshot.

    Reads ``stop_time_update.arrival.delay`` (falling back to the departure
    delay) for every entity carrying a ``trip_update`` (Requirement 9.2). The
    resulting snapshot is stamped with the current UTC time as ``fetched_at``.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)

    feed_timestamp = feed.header.timestamp if feed.header.HasField("timestamp") else None

    delays: List[StopTimeDelay] = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        trip_update = entity.trip_update
        trip = trip_update.trip
        trip_id = trip.trip_id
        route_id = trip.route_id

        for stu in trip_update.stop_time_update:
            # Preferred: an explicit GTFS-RT delay (arrival, else departure).
            delay_seconds: Optional[int] = None
            if stu.HasField("arrival") and stu.arrival.HasField("delay"):
                delay_seconds = stu.arrival.delay
            elif stu.HasField("departure") and stu.departure.HasField("delay"):
                delay_seconds = stu.departure.delay

            # Fallback: some feeds (BKK) report an absolute predicted ``time``
            # (epoch seconds) with no ``delay``. Capture it so a schedule join
            # can derive the delay later (resolve_delays_against_schedule).
            predicted_time: Optional[int] = None
            if delay_seconds is None:
                if stu.HasField("arrival") and stu.arrival.HasField("time"):
                    predicted_time = stu.arrival.time
                elif stu.HasField("departure") and stu.departure.HasField("time"):
                    predicted_time = stu.departure.time

            stop_sequence = (
                stu.stop_sequence if stu.HasField("stop_sequence") else None
            )
            delays.append(
                StopTimeDelay(
                    trip_id=trip_id,
                    route_id=route_id,
                    stop_id=stu.stop_id,
                    stop_sequence=stop_sequence,
                    delay_seconds=delay_seconds,
                    predicted_time=predicted_time,
                )
            )

    return RealtimeSnapshot(
        fetched_at=_utcnow(),
        feed_timestamp=feed_timestamp,
        stop_time_delays=delays,
    )


def resolve_delays_against_schedule(
    snapshot: "RealtimeSnapshot", repository, *, service_date: Optional[datetime] = None
) -> "RealtimeSnapshot":
    """Fill in ``delay_seconds`` for observations that only carried a ``time``.

    Feeds like BKK report an absolute predicted ``time`` (epoch seconds) instead
    of a GTFS-RT ``delay``. For those observations we compute
    ``delay_seconds = predicted_time - scheduled_time`` where ``scheduled_time``
    comes from the static ``stop_times`` for that ``(trip_id, stop)`` (matched by
    ``stop_sequence`` first, else ``stop_id``). Observations that already have an
    explicit ``delay_seconds`` are left untouched, as are those we cannot match
    to a schedule (they stay ``None`` and are simply ignored downstream).

    Returns a new snapshot; the input is not mutated. On a mock-backed repository
    (no static ``stop_times``) the snapshot is returned unchanged.
    """
    # Nothing to do without a live schedule or without time-only observations.
    if getattr(repository, "is_mock", False):
        return snapshot
    needs_resolution = [
        d for d in snapshot.stop_time_delays
        if d.delay_seconds is None and d.predicted_time is not None
    ]
    if not needs_resolution:
        return snapshot

    # BKK schedule clock strings are Budapest local time; anchor the service
    # date in that zone so parse_gtfs_time -> .timestamp() yields the correct
    # UTC epoch to compare against the feed's UTC predicted_time. Without this
    # the delays are off by the UTC offset (e.g. -120 min in summer).
    base = service_date or _utcnow()
    try:
        from zoneinfo import ZoneInfo

        anchor_date = base.astimezone(ZoneInfo("Europe/Budapest"))
    except Exception:  # pragma: no cover - zoneinfo/tzdata always present here.
        anchor_date = base

    # Local import avoids a module-level import cycle.
    from backend.departure_service import parse_gtfs_time

    # Batch schedule lookup: trip_id -> {"seq": {...}, "stop": {...}} built once.
    sched_index = repository.scheduled_times_index()
    if not sched_index:
        return snapshot

    # Fallback schedule view for when the live trip_id has no static match
    # (BKK reissues trip_ids each schedule version). route_id -> {stop_id:
    # sorted [seconds_since_midnight]}. The anchored day epoch lets us compare
    # these to the feed's absolute predicted_time and pick the closest trip.
    route_stop_index = repository.route_stop_schedule_index()
    day_anchor = anchor_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_epoch = int(day_anchor.timestamp())
    # A GTFS-RT prediction should land within a few hours of its scheduled
    # time; reject nearest matches further off than this so we never attribute
    # a wildly wrong "delay" by snapping to the wrong run of the day.
    _MAX_FALLBACK_SKEW = 3 * 60 * 60  # 3 hours, in seconds

    # Memoize the string->epoch conversion; the feed reuses the same clock
    # strings across thousands of trips.
    epoch_cache: dict[str, Optional[int]] = {}

    def _to_epoch(time_str: str) -> Optional[int]:
        cached = epoch_cache.get(time_str)
        if cached is not None or time_str in epoch_cache:
            return cached
        sched_dt = parse_gtfs_time(time_str, anchor_date)
        epoch = int(sched_dt.timestamp()) if sched_dt is not None else None
        epoch_cache[time_str] = epoch
        return epoch

    def _fallback_delay(obs: "StopTimeDelay") -> Optional[int]:
        """Derive a delay from the nearest static (route, stop) scheduled time.

        Used when the live ``trip_id`` is absent from the static feed. Picks the
        static scheduled time for this route+stop whose day-anchored epoch is
        closest to the feed's ``predicted_time`` and returns the signed
        difference, or ``None`` when nothing is within ``_MAX_FALLBACK_SKEW``.
        """
        if not obs.stop_id:
            return None
        by_stop = route_stop_index.get(str(obs.route_id))
        if not by_stop:
            return None
        secs_list = by_stop.get(str(obs.stop_id))
        if not secs_list:
            return None
        best_delay: Optional[int] = None
        best_abs = _MAX_FALLBACK_SKEW + 1
        for secs in secs_list:
            # seconds-since-midnight can exceed 86400 for after-midnight trips;
            # day_epoch + secs reconstructs the absolute scheduled epoch.
            diff = int(obs.predicted_time - (day_epoch + secs))
            if abs(diff) < best_abs:
                best_abs = abs(diff)
                best_delay = diff
        if best_delay is None or best_abs > _MAX_FALLBACK_SKEW:
            return None
        return best_delay

    resolved: List[StopTimeDelay] = []
    matched_exact = 0
    matched_fallback = 0
    for d in snapshot.stop_time_delays:
        if d.delay_seconds is not None or d.predicted_time is None:
            resolved.append(d)
            continue
        delay: Optional[int] = None
        entry = sched_index.get(d.trip_id)
        if entry is not None:
            time_str = None
            if d.stop_sequence is not None:
                time_str = entry["seq"].get(int(d.stop_sequence))
            if time_str is None and d.stop_id:
                time_str = entry["stop"].get(str(d.stop_id))
            if time_str is not None:
                scheduled_epoch = _to_epoch(time_str)
                if scheduled_epoch is not None:
                    delay = int(d.predicted_time - scheduled_epoch)
                    matched_exact += 1
        # Exact trip-id join missed (unknown trip or unmatched stop): fall back
        # to the nearest static scheduled time for this (route, stop).
        if delay is None:
            delay = _fallback_delay(d)
            if delay is not None:
                matched_fallback += 1
        if delay is None:
            resolved.append(d)  # still unresolved -> leave None, ignored later
            continue
        resolved.append(dataclasses.replace(d, delay_seconds=delay))

    if matched_fallback:
        logger.info(
            "GTFS-RT delay resolution: %d exact, %d via route/stop fallback "
            "(static feed trip_ids likely drifted from the live feed)",
            matched_exact,
            matched_fallback,
        )

    return dataclasses.replace(snapshot, stop_time_delays=resolved)


class RealtimeService:
    """Polls GTFS-RT TripUpdates and exposes the latest snapshot + status.

    Parameters
    ----------
    url:
        The GTFS-RT TripUpdates ``.pb`` endpoint. Defaults to
        :data:`config.GTFS_RT_TRIP_UPDATES_URL`.
    api_key:
        Optional BKK API key appended as a query parameter. Defaults to
        :data:`config.BKK_API_KEY`.
    poll_interval_seconds:
        How often :meth:`start` fetches a new snapshot.
    staleness_threshold_seconds:
        A snapshot older than this flips :pyattr:`status` to ``mock``.
    timeout:
        Per-request timeout for the outbound HTTP call.
    client:
        Optional pre-built ``httpx.Client`` (injected in tests). When omitted a
        short-lived client is created per fetch.
    clock:
        Injectable ``now`` provider for deterministic staleness tests.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        poll_interval_seconds: Optional[float] = None,
        staleness_threshold_seconds: Optional[float] = None,
        timeout: Optional[float] = None,
        client: Optional[httpx.Client] = None,
        clock: Clock = _utcnow,
        repository: Optional[object] = None,
    ) -> None:
        self.url = url or config.GTFS_RT_TRIP_UPDATES_URL
        self.api_key = api_key if api_key is not None else config.BKK_API_KEY
        self.poll_interval_seconds = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else config.POLL_INTERVAL_SECONDS
        )
        self.staleness_threshold_seconds = (
            staleness_threshold_seconds
            if staleness_threshold_seconds is not None
            else config.STALENESS_THRESHOLD_SECONDS
        )
        self.timeout = timeout if timeout is not None else config.HTTP_TIMEOUT_SECONDS
        self._client = client
        self._clock = clock
        # Optional static schedule, used to derive delays for feeds that report
        # an absolute predicted time instead of a GTFS-RT delay field (e.g. BKK).
        self._repository = repository

        self._snapshot: Optional[RealtimeSnapshot] = None
        self._last_poll_ok: bool = False
        self._lock = threading.Lock()

        # Background polling machinery (only used when start() is called).
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # -- public API ---------------------------------------------------------

    def poll_once(self) -> bool:
        """Perform a single fetch/parse/cache cycle.

        Returns ``True`` when a snapshot was successfully fetched and cached,
        ``False`` when the poll failed (network/timeout/parse). Never raises:
        failures are logged and leave any previously cached snapshot in place so
        callers can decide, via :pyattr:`status`, whether it is still fresh
        (Requirements 9.3, 10.2).
        """
        try:
            raw = self._fetch()
            snapshot = parse_trip_updates(raw)
            # Feeds that report absolute predicted times (no explicit delay)
            # need a schedule join to derive delay_seconds (e.g. BKK).
            if self._repository is not None:
                snapshot = resolve_delays_against_schedule(
                    snapshot, self._repository, service_date=self._clock()
                )
            # Stamp with the service's clock so staleness is measured against the
            # same time source (deterministic in tests, real wall-clock in prod).
            snapshot = dataclasses.replace(snapshot, fetched_at=self._clock())
        except Exception as exc:  # noqa: BLE001 - degrade, do not fail (R9.3).
            logger.warning("GTFS-RT poll failed for %s: %s", self.url, exc)
            with self._lock:
                self._last_poll_ok = False
            return False

        with self._lock:
            self._snapshot = snapshot
            self._last_poll_ok = True
        return True

    def latest_trip_updates(self) -> Optional[RealtimeSnapshot]:
        """Return the most recently cached snapshot, or ``None`` if never polled."""
        with self._lock:
            return self._snapshot

    @property
    def status(self) -> DataStatus:
        """The current data-status for realtime data.

        ``source`` is ``"realtime"`` only when the last poll succeeded AND the
        cached snapshot is still fresh; otherwise it is ``"mock"`` -- covering
        both the poll-failure and the staleness cases (Requirements 1.2, 1.3,
        9.3, 10.1, 10.2). ``stale`` reflects snapshot age independently of poll
        success so the UI can distinguish "aged out" from "never fetched".
        """
        now = self._clock()
        with self._lock:
            snapshot = self._snapshot
            last_poll_ok = self._last_poll_ok

        if snapshot is None:
            # Nothing fetched yet -> mock, and there is no snapshot to age.
            return DataStatus(source="mock", fetched_at=now, stale=True)

        stale = snapshot.is_stale(self.staleness_threshold_seconds, now)
        is_live = last_poll_ok and not stale
        return DataStatus(
            source="realtime" if is_live else "mock",
            fetched_at=snapshot.fetched_at,
            stale=stale,
        )

    @property
    def is_live(self) -> bool:
        """Convenience boolean mirroring ``status.source == "realtime"``."""
        return self.status.source == "realtime"

    def start(self) -> None:
        """Launch the background polling loop in a daemon thread.

        Idempotent: calling it again while already running is a no-op. Each
        iteration runs :meth:`poll_once` then waits ``poll_interval_seconds``
        (interruptible via :meth:`stop`).
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="gtfs-rt-poller", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: Optional[float] = None) -> None:
        """Signal the background loop to stop and join the thread."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
            self._thread = None

    # -- internals ----------------------------------------------------------

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            # Interruptible sleep so stop() returns promptly.
            self._stop_event.wait(self.poll_interval_seconds)

    def _fetch(self) -> bytes:
        """Fetch the raw protobuf bytes from the GTFS-RT endpoint."""
        params = {}
        if self.api_key:
            params["key"] = self.api_key

        if self._client is not None:
            response = self._client.get(self.url, params=params, timeout=self.timeout)
        else:
            response = httpx.get(self.url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.content


__all__ = [
    "RealtimeService",
    "RealtimeSnapshot",
    "StopTimeDelay",
    "parse_trip_updates",
    "resolve_delays_against_schedule",
]
