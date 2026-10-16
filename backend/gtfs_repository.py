"""In-memory query layer over the static GTFS tables (task 4).

:class:`GtfsRepository` sits on top of :class:`~backend.gtfs_loader.GtfsTables`
(produced by :class:`~backend.gtfs_loader.GtfsLoader`) and answers the
schedule-shaped questions the API needs:

* :meth:`list_lines`            -> all lines with mode + color (R2.3, R4.1)
* :meth:`stops_for_line`        -> stops scoped to a single route (R4.2, R4.3)
* :meth:`stops_near`            -> stops within a haversine radius (R2.1)
* :meth:`route_shape`           -> ordered polyline for map highlight (R2.4)
* :meth:`scheduled_stop_times`  -> a trip's scheduled stop_times rows
* :meth:`mode_of`               -> a route's transit mode (R2.2 color)

Mode/color decisions are delegated to the centralized :mod:`backend.colors`
module so the map, lists, and legend never drift (design "Property 8").

## Resilience / mock fallback (Requirements 9.3, 10.3)

The repository is normally constructed from real GTFS tables, but the app must
never crash when the static feed is missing or corrupt. :meth:`from_zip`
catches :class:`~backend.gtfs_loader.GtfsLoadError` and returns a repository
backed by :class:`~backend.mock_data.MockDataProvider`; :attr:`is_mock` then
reports ``True`` so the caller can flag the data status accordingly. All query
methods work identically against either backing, so callers are agnostic to
which one they got.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional, Union

import pandas as pd

from backend import colors
from backend.gtfs_loader import GtfsLoader, GtfsLoadError, GtfsTables
from backend.mock_data import MockDataProvider
from backend.models import Direction, Line, LineDirections, Mode, Stop

# A point on a route polyline as (lat, lon). The design refers to this as
# ``LatLon``; we keep it a plain tuple to stay JSON/Leaflet friendly.
LatLon = tuple[float, float]

# Mean Earth radius in metres, for the haversine distance.
_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in metres."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


class GtfsRepository:
    """Query layer over static GTFS tables, with a mock fallback path.

    Construct directly from :class:`GtfsTables` for the live path, or via
    :meth:`from_zip` / :meth:`mock` which handle fallback and testing.
    """

    def __init__(self, tables: GtfsTables) -> None:
        self._tables = tables
        self._mock: Optional[MockDataProvider] = None
        self._build_indexes()

    # -- construction / fallback ---------------------------------------------

    @classmethod
    def from_zip(
        cls,
        zip_path: Union[str, Path],
        loader: Optional[GtfsLoader] = None,
        *,
        mock_seed: Optional[int] = None,
    ) -> "GtfsRepository":
        """Build from a GTFS zip, falling back to mock data on load failure.

        If the zip is missing or corrupt (:class:`GtfsLoadError`), a mock-backed
        repository is returned instead of raising, so the backend stays up
        (Requirements 9.3, 10.3). Check :attr:`is_mock` to know which you got.
        """
        loader = loader or GtfsLoader()
        try:
            tables = loader.load(zip_path)
        except GtfsLoadError:
            return cls.mock() if mock_seed is None else cls.mock(seed=mock_seed)
        return cls(tables)

    @classmethod
    def mock(cls, seed: Optional[int] = None) -> "GtfsRepository":
        """Return a repository backed entirely by :class:`MockDataProvider`."""
        instance = cls.__new__(cls)
        instance._tables = None  # type: ignore[assignment]
        instance._mock = (
            MockDataProvider() if seed is None else MockDataProvider(seed=seed)
        )
        return instance

    @property
    def is_mock(self) -> bool:
        """True when this repository is serving mock data, not real GTFS."""
        return self._mock is not None

    # -- index building (live path only) -------------------------------------

    def _build_indexes(self) -> None:
        """Precompute lookups so per-request queries stay cheap.

        Builds:
          * ``_routes_by_id``  : route_id -> route row (dict)
          * ``_stops_by_id``   : stop_id -> Stop
          * ``_trips_by_route``: route_id -> list of trip_ids
          * ``_rep_trip``      : route_id -> representative trip_id (most stops)
          * ``_stop_ids_by_trip``: trip_id -> ordered list of stop_ids

        ``_stop_ids_by_trip`` lets ``stops_for_line`` resolve a trip's stops in
        O(1) instead of scanning the whole ``stop_times`` frame per trip
        (the O(trips × rows) defect this index fixes).
        """
        routes = self._tables.routes
        trips = self._tables.trips
        stops = self._tables.stops
        stop_times = self._tables.stop_times

        self._routes_by_id = {
            str(row["route_id"]): row for _, row in routes.iterrows()
        }

        self._stops_by_id: dict[str, Stop] = {}
        for _, row in stops.iterrows():
            lat, lon = row.get("stop_lat"), row.get("stop_lon")
            if pd.isna(lat) or pd.isna(lon):
                continue
            stop_id = str(row["stop_id"])
            self._stops_by_id[stop_id] = Stop(
                stop_id=stop_id,
                name=str(row.get("stop_name", "")),
                lat=float(lat),
                lon=float(lon),
            )

        # route_id -> [trip_id, ...]
        self._trips_by_route: dict[str, list[str]] = {}
        for _, row in trips.iterrows():
            self._trips_by_route.setdefault(str(row["route_id"]), []).append(
                str(row["trip_id"])
            )

        # Pick a representative trip per route: the one visiting the most stops,
        # so stops_for_line / route_shape cover the full line.
        counts = (
            stop_times.groupby("trip_id").size()
            if not stop_times.empty
            else pd.Series(dtype=int)
        )
        self._rep_trip: dict[str, str] = {}
        for route_id, trip_ids in self._trips_by_route.items():
            best_trip, best_count = None, -1
            for trip_id in trip_ids:
                count = int(counts.get(trip_id, 0))
                if count > best_count:
                    best_trip, best_count = trip_id, count
            if best_trip is not None:
                self._rep_trip[route_id] = best_trip

        # trip_id -> ordered [stop_id, ...] (by stop_sequence). Built once with a
        # single sort + groupby so stops_for_line never scans stop_times per trip.
        self._stop_ids_by_trip: dict[str, list[str]] = {}
        if not stop_times.empty:
            st_sorted = stop_times.sort_values(["trip_id", "stop_sequence"])
            grouped = st_sorted.groupby("trip_id", sort=False)["stop_id"].apply(list)
            self._stop_ids_by_trip = {
                str(trip_id): [str(sid) for sid in stop_ids]
                for trip_id, stop_ids in grouped.items()
            }

        # trip_id -> service_id, so trips can be filtered to services that run on
        # a given date (GTFS ships duplicate trips across weekday/weekend/holiday
        # calendars; without this filter a stop shows several "same-minute"
        # departures that are really the same run on different service days).
        self._service_by_trip: dict[str, str] = {}
        if "service_id" in trips.columns:
            for _, row in trips.iterrows():
                self._service_by_trip[str(row["trip_id"])] = str(row.get("service_id", ""))

        # trip_id -> direction_id (0/1 as str, "" when the feed omits it). Used
        # to split a line's stops per travel direction so the two directions are
        # never mixed into one list (Requirement 4).
        self._direction_by_trip: dict[str, str] = {}
        # (route_id, direction) -> [trip_id, ...]
        self._trips_by_route_dir: dict[tuple[str, str], list[str]] = {}
        if "direction_id" in trips.columns:
            for _, row in trips.iterrows():
                tid = str(row["trip_id"])
                raw = row.get("direction_id")
                direction = "" if pd.isna(raw) else str(int(raw))
                self._direction_by_trip[tid] = direction
                rid = str(row["route_id"])
                self._trips_by_route_dir.setdefault((rid, direction), []).append(tid)
        else:
            # No direction column: everything is a single, unnamed direction.
            for rid, tids in self._trips_by_route.items():
                self._trips_by_route_dir[(rid, "")] = list(tids)
                for tid in tids:
                    self._direction_by_trip[tid] = ""

    # -- lines ----------------------------------------------------------------

    def list_lines(self) -> list[Line]:
        """Return every line with its mode and display color (R2.3, R4.1).

        Routes whose ``route_type`` is not a modeled mode (e.g. HÉV, ferry) are
        skipped rather than mis-colored.
        """
        if self._mock is not None:
            return self._mock.lines()

        lines: list[Line] = []
        for route_id, row in self._routes_by_id.items():
            mode = colors.mode_from_route_type(row.get("route_type"))
            if mode is None:
                continue
            color = colors.color_for_mode(mode, row.get("route_color"))
            lines.append(
                Line(
                    route_id=route_id,
                    short_name=str(row.get("route_short_name", route_id)),
                    mode=mode,
                    color=color,
                )
            )
        return lines

    def mode_of(self, route_id: str) -> Optional[Mode]:
        """Return the transit mode for ``route_id`` (R2.2), or ``None``."""
        if self._mock is not None:
            return self._mock.mode_of(route_id)
        row = self._routes_by_id.get(route_id)
        if row is None:
            return None
        return colors.mode_from_route_type(row.get("route_type"))

    def color_of(self, route_id: str) -> Optional[str]:
        """Return the display color for ``route_id`` (R2.2), or ``None``."""
        if self._mock is not None:
            line = next(
                (l for l in self._mock.lines() if l.route_id == route_id), None
            )
            return line.color if line else None
        row = self._routes_by_id.get(route_id)
        if row is None:
            return None
        mode = colors.mode_from_route_type(row.get("route_type"))
        if mode is None:
            return None
        return colors.color_for_mode(mode, row.get("route_color"))

    # -- stops ----------------------------------------------------------------

    def stops_for_line(self, route_id: str) -> list[Stop]:
        """Return only the stops served by ``route_id`` (R4.2, R4.3).

        Stops are returned in schedule (``stop_sequence``) order along the
        route's representative trip, de-duplicated. Empty list for an unknown
        route.
        """
        if self._mock is not None:
            return self._mock.stops_for_line(route_id)

        trip_ids = self._trips_by_route.get(route_id)
        if not trip_ids:
            return []

        # Union of all stops across the route's trips, ordered by the
        # representative trip's sequence and then first appearance. Per-trip stop
        # order comes from the precomputed ``_stop_ids_by_trip`` index, so this
        # never scans the full stop_times frame (was O(trips × rows)).
        rep = self._rep_trip.get(route_id)
        ordered_ids: list[str] = []
        seen: set[str] = set()

        def _append_from_trip(trip_id: str) -> None:
            for stop_id in self._stop_ids_by_trip.get(trip_id, ()):
                if stop_id not in seen:
                    seen.add(stop_id)
                    ordered_ids.append(stop_id)

        if rep is not None:
            _append_from_trip(rep)
        for trip_id in trip_ids:
            if trip_id != rep:
                _append_from_trip(trip_id)

        return [
            self._stops_by_id[sid] for sid in ordered_ids if sid in self._stops_by_id
        ]

    def available_directions(self, route_id: str) -> list[str]:
        """Return the direction ids served by ``route_id``, sorted.

        Values are the GTFS ``direction_id`` strings ("0"/"1"), or a single
        "" entry when the feed omits the column. Empty list for an unknown
        route or a mock-backed repository.
        """
        if self._mock is not None:
            # Mock catalog models a single direction per line.
            return [""] if self._mock.stops_for_line(route_id) else []
        dirs = [
            d for (rid, d) in self._trips_by_route_dir if rid == route_id
        ]
        return sorted(set(dirs))

    def _ordered_stop_ids_for_direction(
        self, route_id: str, direction: str
    ) -> list[str]:
        """Ordered, de-duplicated stop ids for one direction of a route.

        Ordering follows the direction's representative trip (the one visiting
        the most stops), then first-appearance across its remaining trips, so
        the sequence runs start terminus -> end terminus with no duplicates.
        """
        trip_ids = self._trips_by_route_dir.get((route_id, direction), [])
        if not trip_ids:
            return []

        counts = {t: len(self._stop_ids_by_trip.get(t, ())) for t in trip_ids}
        rep = max(trip_ids, key=lambda t: counts.get(t, 0))

        ordered: list[str] = []
        seen: set[str] = set()

        def _append(trip_id: str) -> None:
            for sid in self._stop_ids_by_trip.get(trip_id, ()):  # noqa: E501
                if sid not in seen:
                    seen.add(sid)
                    ordered.append(sid)

        _append(rep)
        for trip_id in trip_ids:
            if trip_id != rep:
                _append(trip_id)
        return ordered

    def stops_for_line_direction(
        self, route_id: str, direction: str
    ) -> list[Stop]:
        """Stops of a *single* direction, ordered start -> end, no duplicates.

        This is the direction-scoped counterpart of :meth:`stops_for_line`
        answering Requirement 4.2 (one direction only, no mixed/duplicated
        stops). Empty list for an unknown route/direction.
        """
        if self._mock is not None:
            return self._mock.stops_for_line(route_id)
        return [
            self._stops_by_id[sid]
            for sid in self._ordered_stop_ids_for_direction(route_id, direction)
            if sid in self._stops_by_id
        ]

    def line_directions(
        self, route_id: str, direction: Optional[str] = None
    ) -> Optional[LineDirections]:
        """Return a line's directions + the active direction's stops (R4).

        ``direction`` selects the active direction; when ``None`` the lowest
        available direction id is used (e.g. "0"). The returned
        :class:`LineDirections` carries, for every available direction, its
        start/end terminus names, plus the active direction's ordered stops.
        Returns ``None`` for an unknown route.
        """
        available = self.available_directions(route_id)
        if not available:
            return None

        active = direction if direction in available else available[0]

        directions: list[Direction] = []
        for d in available:
            stops = self.stops_for_line_direction(route_id, d)
            directions.append(
                Direction(
                    direction_id=d,
                    start_terminus=stops[0].name if stops else None,
                    end_terminus=stops[-1].name if stops else None,
                )
            )

        return LineDirections(
            route_id=route_id,
            active_direction=active,
            directions=directions,
            stops=self.stops_for_line_direction(route_id, active),
        )

    def stops_near(self, lat: float, lon: float, radius_m: int) -> list[Stop]:
        """Return stops within ``radius_m`` metres of ``(lat, lon)`` (R2.1).

        Results are sorted nearest-first. ``radius_m`` <= 0 yields no stops.
        """
        if radius_m <= 0:
            return []

        if self._mock is not None:
            candidates = self._mock.all_stops()
        else:
            candidates = list(self._stops_by_id.values())

        within: list[tuple[float, Stop]] = []
        for stop in candidates:
            dist = haversine_m(lat, lon, stop.lat, stop.lon)
            if dist <= radius_m:
                within.append((dist, stop))
        within.sort(key=lambda pair: pair[0])
        return [stop for _, stop in within]

    # -- shape / stop_times ---------------------------------------------------

    def route_shape(self, route_id: str) -> list[LatLon]:
        """Return an ordered ``(lat, lon)`` polyline for the route (R2.4).

        The shape is derived from the ordered stops of the route's
        representative trip, which is sufficient to highlight the line on the
        map without requiring ``shapes.txt``. Empty list for an unknown route.
        """
        return [(stop.lat, stop.lon) for stop in self.stops_for_line(route_id)]

    def scheduled_stop_times(self, trip_id: str) -> pd.DataFrame:
        """Return the scheduled ``stop_times`` rows for ``trip_id``.

        Rows are sorted by ``stop_sequence``. Empty DataFrame for an unknown
        trip or when running on mock data (mock has no stop_times table).
        """
        if self._mock is not None:
            return pd.DataFrame()
        st = self._tables.stop_times
        rows = st[st["trip_id"].astype(str) == str(trip_id)]
        return rows.sort_values("stop_sequence").reset_index(drop=True)

    def scheduled_times_index(self) -> dict:
        """Return ``trip_id -> {"seq": {seq: time_str}, "stop": {stop_id: time_str}}``.

        Built once (single sort + groupby over ``stop_times``) and cached, so
        callers that need scheduled times for many trips at once (e.g. the
        GTFS-RT delay resolver) avoid the O(trips × rows) per-trip scan that
        ``scheduled_stop_times`` would incur. Empty dict on mock-backed repos.

        Each entry maps a trip to its stops' scheduled clock strings, keyed both
        by ``stop_sequence`` and by ``stop_id`` so a realtime observation can be
        matched by whichever identifier it carries. Arrival time is preferred,
        falling back to departure time.
        """
        cached = getattr(self, "_scheduled_times_index", None)
        if cached is not None:
            return cached
        index: dict = {}
        if self._mock is None:
            st = self._tables.stop_times
            if not st.empty:
                cols = ["trip_id", "stop_id", "stop_sequence"]
                has_arr = "arrival_time" in st.columns
                has_dep = "departure_time" in st.columns
                if has_arr:
                    cols.append("arrival_time")
                if has_dep:
                    cols.append("departure_time")
                sub = st[cols]
                for row in sub.itertuples(index=False):
                    trip_id = str(row.trip_id)
                    entry = index.get(trip_id)
                    if entry is None:
                        entry = {"seq": {}, "stop": {}}
                        index[trip_id] = entry
                    time_str = None
                    if has_arr:
                        time_str = getattr(row, "arrival_time", None)
                    if (time_str is None or (isinstance(time_str, float) and time_str != time_str)) and has_dep:
                        time_str = getattr(row, "departure_time", None)
                    if time_str is None or (isinstance(time_str, float) and time_str != time_str):
                        continue
                    seq = row.stop_sequence
                    if seq is not None and not (isinstance(seq, float) and seq != seq):
                        entry["seq"][int(seq)] = str(time_str)
                    sid = row.stop_id
                    if sid is not None:
                        entry["stop"][str(sid)] = str(time_str)
        self._scheduled_times_index = index
        return index

    def _trip_schedule_index(self) -> dict:
        """Return ``trip_id -> [ (stop_id, stop_sequence, seconds_since_midnight) ]``.

        One ordered list per trip (sorted by ``stop_sequence``), built once with
        a single sort + groupby and cached. ``seconds_since_midnight`` is the
        scheduled arrival (falling back to departure), parsed from the GTFS clock
        string; it can exceed 86400 for trips running past midnight. Used to
        anchor the arrival timeline and the next-trip lookup on a *single real
        trip* rather than a synthetic union across trips. Empty on mock repos.
        """
        cached = getattr(self, "_trip_sched_idx", None)
        if cached is not None:
            return cached
        index: dict = {}
        if self._mock is None:
            st = self._tables.stop_times
            if not st.empty:
                has_arr = "arrival_time" in st.columns
                has_dep = "departure_time" in st.columns
                cols = ["trip_id", "stop_id", "stop_sequence"]
                if has_arr:
                    cols.append("arrival_time")
                if has_dep:
                    cols.append("departure_time")
                sub = st[cols].sort_values(["trip_id", "stop_sequence"])
                for row in sub.itertuples(index=False):
                    trip_id = str(row.trip_id)
                    time_str = None
                    if has_arr:
                        time_str = getattr(row, "arrival_time", None)
                    if (time_str is None or (isinstance(time_str, float) and time_str != time_str)) and has_dep:
                        time_str = getattr(row, "departure_time", None)
                    secs = _gtfs_seconds(time_str)
                    if secs is None:
                        continue
                    seq = row.stop_sequence
                    seq_val = int(seq) if seq is not None and not (isinstance(seq, float) and seq != seq) else None
                    index.setdefault(trip_id, []).append(
                        (str(row.stop_id), seq_val, secs)
                    )
        self._trip_sched_idx = index
        return index

    def trip_schedule(self, trip_id: str) -> list[tuple]:
        """Ordered ``[(stop_id, stop_sequence, seconds_since_midnight)]`` for a trip.

        Empty list for an unknown trip or a mock-backed repository.
        """
        return list(self._trip_schedule_index().get(str(trip_id), ()))

    def route_stop_schedule_index(self) -> dict:
        """Return ``route_id -> {stop_id: sorted [seconds_since_midnight, ...]}``.

        A route-level view of the static schedule, aggregating every trip's
        scheduled stop times by stop. Built once from the cached trip-schedule
        index and memoized. Empty on mock-backed repositories.

        This exists for GTFS-RT delay resolution resilience: BKK's realtime feed
        assigns fresh ``trip_id`` values each schedule version, so a live
        observation's trip often has *no* match in a slightly older static feed.
        The stops themselves are stable, so when an exact trip-id join fails we
        can still derive a delay by comparing the feed's predicted time to the
        nearest static scheduled time for that ``(route_id, stop_id)``.
        """
        cached = getattr(self, "_route_stop_sched_idx", None)
        if cached is not None:
            return cached
        index: dict = {}
        if self._mock is None:
            trip_index = self._trip_schedule_index()
            for route_id, trip_ids in self._trips_by_route.items():
                by_stop: dict[str, list[int]] = {}
                for trip_id in trip_ids:
                    for stop_id, _seq, secs in trip_index.get(str(trip_id), ()):  # noqa: E501
                        by_stop.setdefault(str(stop_id), []).append(int(secs))
                for secs_list in by_stop.values():
                    secs_list.sort()
                if by_stop:
                    index[str(route_id)] = by_stop
        self._route_stop_sched_idx = index
        return index

    def trips_for_route(self, route_id: str) -> list[str]:
        """Return the trip_ids serving ``route_id`` (empty for unknown/mock)."""
        if self._mock is not None:
            return []
        return list(self._trips_by_route.get(route_id, ()))

    def _services_active_on(self, date_yyyymmdd: str) -> Optional[set]:
        """Return the set of service_ids active on ``date_yyyymmdd`` (or None).

        Supports both the ``calendar_dates.txt`` shape (service_id/date/
        exception_type, where 1 = added) and the ``calendar.txt`` weekday-flags
        shape. Returns ``None`` when the calendar is absent/unusable, signalling
        callers to skip service-day filtering rather than hide everything.
        Cached per date.
        """
        cache = getattr(self, "_services_active_cache", None)
        if cache is None:
            cache = {}
            self._services_active_cache = cache
        if date_yyyymmdd in cache:
            return cache[date_yyyymmdd]

        result: Optional[set] = None
        cal = getattr(self._tables, "calendar", None)
        if cal is not None and not cal.empty:
            cols = set(cal.columns)
            if {"service_id", "date", "exception_type"} <= cols:
                # calendar_dates.txt form: exception_type 1 = service added.
                day = cal[cal["date"].astype(str) == str(date_yyyymmdd)]
                added = day[day["exception_type"].astype(str) == "1"]
                result = {str(s) for s in added["service_id"]}
            elif {"service_id", "start_date", "end_date"} <= cols:
                # calendar.txt form: weekday flags + date range.
                import datetime as _dt
                try:
                    d = _dt.datetime.strptime(str(date_yyyymmdd), "%Y%m%d").date()
                    weekday_col = [
                        "monday", "tuesday", "wednesday", "thursday",
                        "friday", "saturday", "sunday",
                    ][d.weekday()]
                except ValueError:
                    weekday_col = None
                if weekday_col and weekday_col in cols:
                    active = cal[
                        (cal[weekday_col].astype(str) == "1")
                        & (cal["start_date"].astype(str) <= str(date_yyyymmdd))
                        & (cal["end_date"].astype(str) >= str(date_yyyymmdd))
                    ]
                    result = {str(s) for s in active["service_id"]}

        # Only trust a non-empty result; an empty set likely means the calendar
        # doesn't cover this date, so fall back to no filtering (None).
        cache[date_yyyymmdd] = result if result else None
        return cache[date_yyyymmdd]

    def trips_for_route_on_date(
        self, route_id: str, date_yyyymmdd: str
    ) -> list[str]:
        """Trips on ``route_id`` whose service runs on ``date_yyyymmdd``.

        Falls back to all trips for the route when the calendar can't resolve
        active services for that date (so we never hide everything).
        """
        trips = self.trips_for_route(route_id)
        active = self._services_active_on(date_yyyymmdd)
        if not active:
            return trips
        return [
            t for t in trips
            if self._service_by_trip.get(t, "") in active
        ]


def _gtfs_seconds(value: object) -> "Optional[int]":
    """Parse a GTFS ``HH:MM:SS`` clock string to seconds since midnight.

    Hours >= 24 are preserved (past-midnight service), so the result can exceed
    86400. Returns ``None`` for blank/NaN/malformed values.
    """
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 3:
        return None
    try:
        h, m, sec = (int(p) for p in parts)
    except ValueError:
        return None
    return h * 3600 + m * 60 + sec


__all__ = ["GtfsRepository", "LatLon", "haversine_m"]
