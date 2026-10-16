"""Upcoming-departures assembly for a selected stop (task 11).

:class:`DepartureService` answers Requirement 5.2: for a chosen ``(route_id,
stop_id)`` it lists the next few departures with their *scheduled* time, the
*current delay*, and the resulting *expected* time. It does this by merging two
data sources already built in earlier tasks:

* the **static schedule** via :class:`~backend.gtfs_repository.GtfsRepository`
  (``scheduled_stop_times`` per trip, ``stops_for_line`` to find the trips that
  serve the stop), and
* the **live delays** via a :class:`~backend.realtime_service.RealtimeSnapshot`
  (per trip/stop ``delay_seconds``), when one is available.

The core invariant is simple and is what the unit tests pin down:

    expected = scheduled + delay

GTFS ``stop_times`` carry clock strings like ``08:03:00`` (and, legally, values
past ``24:00:00`` for trips after midnight). Those are combined with a service
date anchor to produce absolute :class:`~datetime.datetime` departure times so
the frontend can render and sort them directly.

## Resilience (Requirements 1.3, 9.3, 10.3)

When the repository is running on mock data, or the requested route/stop has no
usable static schedule, the service falls back to
:class:`~backend.mock_data.MockDataProvider` departures so the UI always has
content. Missing realtime simply means every delay is ``0`` and ``expected ==
scheduled`` -- the schedule is still returned. Delays are matched to schedule
rows by ``(trip_id, stop_id)``, falling back to ``(trip_id, stop_sequence)``
when the feed omits ``stop_id``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from backend.gtfs_repository import GtfsRepository
from backend.mock_data import MockDataProvider
from backend.models import Departure
from backend.realtime_service import RealtimeSnapshot, StopTimeDelay
from zoneinfo import ZoneInfo

_BUDAPEST = ZoneInfo("Europe/Budapest")

logger = logging.getLogger(__name__)


def parse_gtfs_time(value: str, service_date: datetime) -> Optional[datetime]:
    """Convert a GTFS ``HH:MM:SS`` clock string to an absolute datetime.

    GTFS permits hours >= 24 to express trips that run past midnight while still
    belonging to the previous service day (e.g. ``25:10:00`` == 01:10 the next
    calendar day). Those roll over correctly here by adding the overflow to the
    ``service_date`` anchor.

    Returns ``None`` for blank or unparseable values so callers can skip the
    row rather than crash (degrade, do not fail).
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(p) for p in parts)
    except ValueError:
        return None

    anchor = service_date.replace(hour=0, minute=0, second=0, microsecond=0)
    return anchor + timedelta(hours=hours, minutes=minutes, seconds=seconds)


class DepartureService:
    """Builds upcoming :class:`Departure` rows for a selected stop (R5.1, R5.2).

    Parameters
    ----------
    repository:
        The static-schedule query layer. May be a live or mock-backed
        :class:`GtfsRepository`.
    mock:
        Fallback provider used when no usable static schedule exists for the
        request. Defaults to a fresh :class:`MockDataProvider`.
    """

    def __init__(
        self,
        repository: GtfsRepository,
        mock: Optional[MockDataProvider] = None,
    ) -> None:
        self._repo = repository
        self._mock = mock or MockDataProvider()

    def upcoming(
        self,
        route_id: str,
        stop_id: str,
        limit: int = 500,
        *,
        snapshot: Optional[RealtimeSnapshot] = None,
        now: Optional[datetime] = None,
        include_past: bool = True,
    ) -> list[Departure]:
        """Return the next ``limit`` departures at ``stop_id`` on ``route_id``.

        Each :class:`Departure` carries the scheduled time, the current delay in
        minutes (0 when no realtime is available), and ``expected = scheduled +
        delay``. By default (``include_past=True``) *all* departures on the
        active service day are returned -- both already-departed and upcoming --
        ordered by scheduled time (R5.2, R5.3, R5.4). Pass ``include_past=False``
        to keep only departures at or after ``now``.

        Falls back to deterministic mock departures when the repository is
        mock-backed or the route/stop has no static schedule (Requirements 1.3,
        9.3, 10.3).
        """
        if limit <= 0:
            return []

        reference = now or datetime.now(timezone.utc)

        # Mock-backed repository -> use the matching mock catalog directly.
        if self._repo.is_mock:
            return self._mock.departures(route_id, stop_id, limit=limit, now=reference)

        # GTFS stop_times clock strings are Budapest *local* time. Anchor the
        # service day at Budapest midnight (not UTC midnight) so that
        # ``day_anchor + seconds_since_midnight`` yields the correct absolute
        # instant. Using the UTC day here shifted every scheduled time by the
        # UTC offset (e.g. +2h in summer), which both mislabelled departures and
        # wrongly filtered out the next ~2 hours of valid ones.
        local_ref = reference.astimezone(_BUDAPEST)
        day_anchor = local_ref.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        delay_index = self._index_delays(snapshot)

        # Build one candidate departure per trip that stops here, reading the
        # cached per-trip schedule index (O(1) per trip) instead of scanning the
        # full stop_times frame per trip (which was O(trips × rows) and hung on
        # high-frequency lines like the metro).
        departures: list[Departure] = []
        seen_trips: set[str] = set()
        service_date = reference.astimezone(_BUDAPEST).strftime("%Y%m%d")
        for trip_id in self._repo.trips_for_route_on_date(route_id, service_date):
            if trip_id in seen_trips:
                continue
            rows = self._repo.trip_schedule(trip_id)
            # First visit of this trip to the selected stop.
            match = next(((sid, seq, secs) for (sid, seq, secs) in rows if sid == stop_id), None)
            if match is None:
                continue
            seen_trips.add(trip_id)
            _sid, seq, secs = match
            scheduled = day_anchor + timedelta(seconds=secs)
            if not include_past and scheduled < reference:
                continue  # future-only mode: drop already-departed trips
            delay_seconds = self._lookup_delay(delay_index, trip_id, stop_id, seq)
            departures.append(
                Departure(
                    trip_id=trip_id,
                    scheduled=scheduled,
                    delay_min=round(delay_seconds / 60.0, 1),
                    expected=scheduled + timedelta(seconds=delay_seconds),
                )
            )

        if not departures:
            # No static departures for this selection -> mock fallback.
            return self._mock.departures(route_id, stop_id, limit=limit, now=reference)

        departures.sort(key=lambda d: (d.scheduled, d.trip_id))
        return departures[:limit]

    # -- internals ------------------------------------------------------------

    def _schedule_rows_for_stop(self, route_id: str, stop_id: str) -> pd.DataFrame:
        """Collect this stop's scheduled rows, one row per trip visiting it.

        For every trip serving ``route_id`` we keep only the *first* visit to
        ``stop_id`` (a trip that passes the same stop twice would otherwise
        produce duplicate near-identical departures). The result is one
        scheduled departure per distinct trip that stops here.
        """
        trip_ids = self._repo._trips_by_route.get(route_id, [])
        if not trip_ids:
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        for trip_id in trip_ids:
            st = self._repo.scheduled_stop_times(trip_id)
            if st.empty:
                continue
            at_stop = st[st["stop_id"].astype(str) == str(stop_id)]
            if at_stop.empty:
                continue
            # Keep only the first visit for this trip (dedupe repeated stops).
            frames.append(at_stop.head(1))

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    @staticmethod
    def _index_delays(
        snapshot: Optional[RealtimeSnapshot],
    ) -> dict[tuple, int]:
        """Index a snapshot's delays for O(1) lookup by trip/stop.

        Two keys are stored per observation so lookups can fall back when the
        feed omits ``stop_id`` but carries ``stop_sequence``:
          * ``("s", trip_id, stop_id)``
          * ``("q", trip_id, stop_sequence)``
        Observations with a ``None`` delay are skipped.
        """
        index: dict[tuple, int] = {}
        if snapshot is None:
            return index
        for d in snapshot.stop_time_delays:
            if d.delay_seconds is None:
                continue
            if d.stop_id:
                index[("s", d.trip_id, str(d.stop_id))] = d.delay_seconds
            if d.stop_sequence is not None:
                index[("q", d.trip_id, int(d.stop_sequence))] = d.delay_seconds
        return index

    @staticmethod
    def _lookup_delay(
        index: dict[tuple, int],
        trip_id: str,
        stop_id: str,
        stop_sequence: object,
    ) -> int:
        """Return the delay in seconds for a schedule row, or 0 if none."""
        hit = index.get(("s", trip_id, str(stop_id)))
        if hit is not None:
            return hit
        if stop_sequence is not None and not pd.isna(stop_sequence):
            hit = index.get(("q", trip_id, int(stop_sequence)))
            if hit is not None:
                return hit
        return 0


__all__ = ["DepartureService", "parse_gtfs_time"]
