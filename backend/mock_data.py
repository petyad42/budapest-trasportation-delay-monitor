"""Deterministic mock-data provider for the Budapest Transit Monitor.

Whenever a live source is unavailable or stale, the system falls back to mock
data so the UI always has something meaningful to render and the data-status
indicator can honestly report ``mock`` (Requirements 1.3, 9.3, 10.3).

Everything this module produces is **deterministic**: given the same seed and
the same request parameters, it returns byte-for-byte identical output. This
matters for two reasons:

* Tests can assert on stable values without flaky, time-dependent fixtures.
* The UI does not "jitter" between refreshes when running in mock mode; delays
  and departures stay coherent across calls.

Determinism is achieved by seeding a private :class:`random.Random` instance
per logical entity (route, stop, trip) using a stable hash of its identifier,
rather than the global RNG. Absolute wall-clock times (e.g. departure times)
are derived from a caller-supplied ``now`` anchor that defaults to a fixed
epoch, so even timestamps are reproducible unless the caller opts into "live"
relative times.

The provider mirrors the real services' return types exactly (:class:`Line`,
:class:`Stop`, :class:`RouteDelay`, :class:`ProblemScore`, :class:`Departure`,
:class:`TimelineNode`) so it is a drop-in fallback anywhere those are expected.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.models import (
    Departure,
    Line,
    Mode,
    ProblemScore,
    RouteDelay,
    Stop,
    TimelineNode,
)

# A fixed anchor so that, by default, generated timestamps are fully
# reproducible. Callers who want departures relative to the real clock can pass
# their own ``now``.
DEFAULT_NOW = datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc)

# Default seed mixed into every per-entity RNG. Changing it reshuffles the whole
# mock world deterministically.
DEFAULT_SEED = 1337

# Mode-appropriate headways (minutes between departures) used to lay out a
# plausible timetable for each mock stop.
_HEADWAY_MIN: dict[Mode, int] = {
    Mode.METRO: 3,
    Mode.TRAM: 6,
    Mode.TROLLEYBUS: 10,
    Mode.BUS: 8,
}

# Official-ish metro line colors; other modes get their canonical mode color.
_METRO_COLORS: dict[str, str] = {
    "M1": "#FFD800",  # yellow line
    "M2": "#E41F18",  # red line
    "M3": "#005CA9",  # blue line
    "M4": "#4CA22F",  # green line
}

# Mode -> canonical color used by the map/lists (R2.2). Kept in sync with the
# frontend's shared color module conceptually; the real GtfsRepository owns the
# authoritative mapping, this is only the mock catalog's colors.
_MODE_COLOR: dict[Mode, str] = {
    Mode.BUS: "#4FC3F7",  # light blue
    Mode.TRAM: "#FFD800",  # yellow
    Mode.TROLLEYBUS: "#E53935",  # red
    Mode.METRO: "#005CA9",  # overridden per-line below
}

# A small, realistic catalog of Budapest lines. Each entry:
#   (route_id, short_name, mode, [stop names...])
# The stops are laid out along a line; coordinates are generated
# deterministically around the Budapest center so the map has content.
_CATALOG: list[tuple[str, str, Mode, list[str]]] = [
    (
        "M1", "M1", Mode.METRO,
        ["Vörösmarty tér", "Deák Ferenc tér", "Opera", "Oktogon",
         "Kodály körönd", "Hősök tere", "Széchenyi fürdő", "Mexikói út"],
    ),
    (
        "M2", "M2", Mode.METRO,
        ["Déli pályaudvar", "Széll Kálmán tér", "Batthyány tér",
         "Kossuth Lajos tér", "Deák Ferenc tér", "Astoria",
         "Blaha Lujza tér", "Keleti pályaudvar", "Örs vezér tere"],
    ),
    (
        "M3", "M3", Mode.METRO,
        ["Újpest-központ", "Dózsa György út", "Lehel tér", "Nyugati pályaudvar",
         "Arany János utca", "Deák Ferenc tér", "Ferenciek tere",
         "Kálvin tér", "Semmelweis Klinikák", "Nagyvárad tér", "Kőbánya-Kispest"],
    ),
    (
        "M4", "M4", Mode.METRO,
        ["Kelenföld vasútállomás", "Bikás park", "Újbuda-központ",
         "Móricz Zsigmond körtér", "Szent Gellért tér", "Fővám tér",
         "Kálvin tér", "Rákóczi tér", "II. János Pál pápa tér",
         "Keleti pályaudvar"],
    ),
    (
        "4", "4", Mode.TRAM,
        ["Széll Kálmán tér", "Széna tér", "Margit híd", "Jászai Mari tér",
         "Nyugati pályaudvar", "Oktogon", "Wesselényi utca", "Blaha Lujza tér",
         "Rákóczi tér", "Corvin-negyed", "Boráros tér", "Móricz Zsigmond körtér"],
    ),
    (
        "6", "6", Mode.TRAM,
        ["Széll Kálmán tér", "Margit híd", "Jászai Mari tér",
         "Nyugati pályaudvar", "Oktogon", "Blaha Lujza tér", "Corvin-negyed",
         "Boráros tér", "Móricz Zsigmond körtér", "Móricz Zsigmond körtér M"],
    ),
    (
        "47", "47", Mode.TRAM,
        ["Deák Ferenc tér", "Fővám tér", "Szent Gellért tér",
         "Móricz Zsigmond körtér", "Kosztolányi Dezső tér", "Budafok"],
    ),
    (
        "7", "7", Mode.BUS,
        ["Kelenföld vasútállomás", "Tétényi út", "Móricz Zsigmond körtér",
         "Szent Gellért tér", "Ferenciek tere", "Astoria", "Blaha Lujza tér",
         "Keleti pályaudvar", "Bosnyák tér"],
    ),
    (
        "9", "9", Mode.BUS,
        ["Kőbánya alsó", "Népliget", "Corvin-negyed", "Kálvin tér",
         "Ferenciek tere", "Deák Ferenc tér", "Nyugati pályaudvar"],
    ),
    (
        "105", "105", Mode.BUS,
        ["Apor Vilmos tér", "Déli pályaudvar", "Széll Kálmán tér",
         "Clark Ádám tér", "Deák Ferenc tér", "Bajcsy-Zsilinszky út"],
    ),
    (
        "72", "72", Mode.TROLLEYBUS,
        ["Arany János utca", "Nyugati pályaudvar", "Lehel tér",
         "Dózsa György út", "Zugló vasútállomás"],
    ),
    (
        "76", "76", Mode.TROLLEYBUS,
        ["Keleti pályaudvar", "Rottenbiller utca", "Városligeti fasor",
         "Hősök tere", "Egressy tér"],
    ),
]

# Budapest center; mock stop coordinates fan out from here.
_CENTER_LAT = 47.4979
_CENTER_LON = 19.0402


def _stable_seed(*parts: object) -> int:
    """Return a stable, process-independent integer seed for ``parts``.

    ``hash()`` is salted per-process in Python, so we use a SHA-256 digest of
    the joined string parts to guarantee the same seed across runs/machines.
    """
    key = "|".join(str(p) for p in parts)
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _stop_id(route_id: str, index: int) -> str:
    return f"MOCK-{route_id}-S{index}"


class MockDataProvider:
    """Deterministic source of mock lines, stops, delays, and departures.

    Construct once and reuse; all methods are pure with respect to the seed and
    their arguments. Two providers built with the same ``seed`` produce
    identical catalogs and metrics.
    """

    def __init__(self, seed: int = DEFAULT_SEED) -> None:
        self.seed = seed
        # Build the catalog eagerly so line/stop identity is stable and shared
        # across all query methods.
        self._lines: list[Line] = []
        self._stops_by_route: dict[str, list[Stop]] = {}
        self._modes: dict[str, Mode] = {}
        self._short_names: dict[str, str] = {}
        self._build_catalog()

    # -- catalog construction -------------------------------------------------

    def _color_for(self, route_id: str, mode: Mode) -> str:
        if mode is Mode.METRO:
            return _METRO_COLORS.get(route_id, _MODE_COLOR[Mode.METRO])
        return _MODE_COLOR[mode]

    def _build_catalog(self) -> None:
        for route_id, short_name, mode, stop_names in _CATALOG:
            color = self._color_for(route_id, mode)
            self._lines.append(
                Line(route_id=route_id, short_name=short_name, mode=mode, color=color)
            )
            self._modes[route_id] = mode
            self._short_names[route_id] = short_name

            rng = random.Random(_stable_seed(self.seed, "stops", route_id))
            stops: list[Stop] = []
            for index, name in enumerate(stop_names):
                # Deterministic small offsets so stops spread across the city
                # in a stable but line-specific way.
                lat = _CENTER_LAT + rng.uniform(-0.06, 0.06)
                lon = _CENTER_LON + rng.uniform(-0.09, 0.09)
                stops.append(
                    Stop(
                        stop_id=_stop_id(route_id, index),
                        name=name,
                        lat=round(lat, 6),
                        lon=round(lon, 6),
                    )
                )
            self._stops_by_route[route_id] = stops

    # -- catalog queries ------------------------------------------------------

    def lines(self) -> list[Line]:
        """Return the full mock line catalog (R4.1, R2.3)."""
        return list(self._lines)

    def stops_for_line(self, route_id: str) -> list[Stop]:
        """Return only the stops belonging to ``route_id`` (R4.2, R4.3).

        Empty list for an unknown route.
        """
        return list(self._stops_by_route.get(route_id, []))

    def all_stops(self) -> list[Stop]:
        """Return every mock stop across all lines, de-duplicated by id."""
        seen: dict[str, Stop] = {}
        for stops in self._stops_by_route.values():
            for stop in stops:
                seen.setdefault(stop.stop_id, stop)
        return list(seen.values())

    def mode_of(self, route_id: str) -> Optional[Mode]:
        return self._modes.get(route_id)

    # -- delays ---------------------------------------------------------------

    def _route_delay_stats(self, route_id: str) -> tuple[float, float]:
        """Deterministic (avg_delay_min, max_delay_min) for a route."""
        rng = random.Random(_stable_seed(self.seed, "delay", route_id))
        mode = self._modes.get(route_id, Mode.BUS)
        # Metro is usually the most punctual; surface buses drift more.
        base = {
            Mode.METRO: 0.5,
            Mode.TRAM: 1.5,
            Mode.TROLLEYBUS: 2.0,
            Mode.BUS: 2.5,
        }[mode]
        avg = base + rng.uniform(0.0, 4.0)
        # Max is always >= avg by a deterministic margin.
        max_delay = avg + rng.uniform(1.0, 8.0)
        return round(avg, 1), round(max_delay, 1)

    def route_delays(self) -> list[RouteDelay]:
        """Deterministic per-route delay metrics for every line (R1.1, R1.4)."""
        delays: list[RouteDelay] = []
        for line in self._lines:
            avg, mx = self._route_delay_stats(line.route_id)
            delays.append(
                RouteDelay(
                    route_id=line.route_id,
                    short_name=line.short_name,
                    mode=line.mode,
                    avg_delay_min=avg,
                    max_delay_min=mx,
                )
            )
        return delays

    def _problem_score(self, avg: float, mx: float) -> float:
        """Blend avg/max delay into a single comparable score.

        Weighted toward the average delay (sustained pain) but with a max-delay
        component (worst-case spikes), matching the DelayComputer's intent.
        """
        return round(0.7 * avg + 0.3 * mx, 3)

    def problem_scores(self) -> list[ProblemScore]:
        """All routes scored and sorted by descending problem score (R3.1)."""
        scores: list[ProblemScore] = []
        for line in self._lines:
            avg, mx = self._route_delay_stats(line.route_id)
            scores.append(
                ProblemScore(
                    route_id=line.route_id,
                    short_name=line.short_name,
                    mode=line.mode,
                    score=self._problem_score(avg, mx),
                    avg_delay_min=avg,
                )
            )
        scores.sort(key=lambda s: (-s.score, s.route_id))
        return scores

    def top_problematic(self, n: int = 5) -> list[ProblemScore]:
        """Top-``n`` routes by problem score (R3.2). Only routes with delay."""
        scored = [s for s in self.problem_scores() if s.avg_delay_min > 0]
        return scored[: max(n, 0)]

    # -- departures -----------------------------------------------------------

    def departures(
        self,
        route_id: str,
        stop_id: str,
        limit: int = 10,
        now: Optional[datetime] = None,
    ) -> list[Departure]:
        """Deterministic upcoming departures for a stop (R5.1, R5.2).

        Scheduled times are laid out on a mode-appropriate headway starting
        from ``now`` (default: the fixed :data:`DEFAULT_NOW` anchor). Each
        departure gets a deterministic delay derived from the trip id, so the
        expected time is always ``scheduled + delay``.
        """
        if route_id not in self._modes:
            return []
        if not any(s.stop_id == stop_id for s in self._stops_by_route[route_id]):
            return []

        anchor = now or DEFAULT_NOW
        mode = self._modes[route_id]
        headway = _HEADWAY_MIN[mode]
        rng = random.Random(_stable_seed(self.seed, "dep", route_id, stop_id))

        # First departure lands a deterministic 0..headway minutes from now so
        # the timetable does not always start exactly on the anchor.
        first_offset = rng.uniform(0.0, float(headway))

        out: list[Departure] = []
        for i in range(max(limit, 0)):
            scheduled = anchor + timedelta(minutes=first_offset + i * headway)
            trip_id = f"MOCK-{route_id}-{stop_id}-T{i}"
            trip_rng = random.Random(_stable_seed(self.seed, "trip", trip_id))
            # Most trips are on-time-ish; occasional larger delays.
            delay_min = round(trip_rng.choices(
                population=[0.0, trip_rng.uniform(0.5, 3.0), trip_rng.uniform(3.0, 12.0)],
                weights=[0.4, 0.45, 0.15],
                k=1,
            )[0], 1)
            expected = scheduled + timedelta(minutes=delay_min)
            out.append(
                Departure(
                    trip_id=trip_id,
                    scheduled=scheduled,
                    delay_min=delay_min,
                    expected=expected,
                )
            )
        return out

    # -- timeline -------------------------------------------------------------

    def timeline(
        self,
        route_id: str,
        stop_id: str,
        now: Optional[datetime] = None,
    ) -> list[TimelineNode]:
        """Deterministic arrival timeline from ``stop_id`` downstream (R7).

        The first node is the selected stop (``is_current=True``); subsequent
        nodes are the downstream stops on the line in ``stop_sequence`` order,
        each with a deterministic expected arrival. If the selected stop is the
        line terminus there is only the current node (R7.4).
        """
        stops = self._stops_by_route.get(route_id, [])
        start = next((i for i, s in enumerate(stops) if s.stop_id == stop_id), None)
        if start is None:
            return []

        anchor = now or DEFAULT_NOW
        mode = self._modes[route_id]
        # Rough per-segment travel time by mode (minutes between stops).
        seg_min = {
            Mode.METRO: 1.5,
            Mode.TRAM: 2.0,
            Mode.TROLLEYBUS: 2.5,
            Mode.BUS: 2.5,
        }[mode]
        rng = random.Random(_stable_seed(self.seed, "timeline", route_id, stop_id))

        nodes: list[TimelineNode] = []
        cumulative = 0.0
        for seq, stop in enumerate(stops[start:]):
            if seq > 0:
                # Deterministic jitter around the nominal segment time.
                cumulative += seg_min + rng.uniform(-0.4, 0.8)
            expected = anchor + timedelta(minutes=cumulative)
            nodes.append(
                TimelineNode(
                    stop_id=stop.stop_id,
                    name=stop.name,
                    stop_sequence=seq,
                    expected_arrival=expected,
                    is_current=(seq == 0),
                )
            )
        return nodes


__all__ = [
    "MockDataProvider",
    "DEFAULT_NOW",
    "DEFAULT_SEED",
]
