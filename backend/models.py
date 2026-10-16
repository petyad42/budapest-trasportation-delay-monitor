"""Shared Pydantic data models for the Budapest Transit Monitor backend.

These models define the typed, validated JSON contracts that the REST API
returns and that the React frontend consumes. They mirror the "Data Models"
section of the design document exactly.

Every API response is wrapped in a shared envelope (see :func:`envelope`) that
attaches a :class:`DataStatus` object, so the frontend can always tell whether
it is looking at real-time or mock data (Requirement 10.1).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel


class DataStatus(BaseModel):
    """Per-payload provenance flag attached to every API response.

    ``source`` is ``"realtime"`` only when a fresh live snapshot was used to
    build the payload; otherwise it is ``"mock"`` (Requirements 1.2, 1.3,
    10.1, 10.2).
    """

    source: Literal["realtime", "mock"]
    fetched_at: datetime
    stale: bool


class Mode(str, Enum):
    """Transit mode for a line, used for map/list color coding (R2.2)."""

    BUS = "bus"
    TRAM = "tram"
    TROLLEYBUS = "trolleybus"
    METRO = "metro"


class Line(BaseModel):
    """A transit line (route)."""

    route_id: str
    short_name: str
    mode: Mode
    color: str  # hex; metro uses official route_color


class Stop(BaseModel):
    """A transit stop with its geographic position."""

    stop_id: str
    name: str
    lat: float
    lon: float


class Direction(BaseModel):
    """One travel orientation of a line (GTFS ``direction_id``), R4.

    ``direction_id`` is the GTFS value ("0"/"1", or "" when the feed omits it).
    ``start_terminus`` / ``end_terminus`` are the first and last stop names of
    this direction in schedule order; both are ``None`` when the direction has
    no resolvable stops.
    """

    direction_id: str
    start_terminus: Optional[str] = None
    end_terminus: Optional[str] = None


class LineDirections(BaseModel):
    """A line's available directions plus the stops of the active one (R4).

    ``stops`` are the active direction's stops ordered from its start terminus
    to its end terminus, with no duplicates. ``directions`` lists every
    direction the line serves so the UI can enable/disable the flip control.
    """

    route_id: str
    active_direction: str
    directions: list[Direction]
    stops: list[Stop]


class RouteDelay(BaseModel):
    """Current per-route delay metrics in minutes."""

    route_id: str
    short_name: str
    mode: Mode
    avg_delay_min: float
    max_delay_min: float


class ProblemScore(BaseModel):
    """A route's problem score used for the Top-5 ranking (R3)."""

    route_id: str
    short_name: str
    mode: Mode
    score: float
    avg_delay_min: float


class Departure(BaseModel):
    """An upcoming departure at a selected stop."""

    trip_id: str
    scheduled: datetime
    delay_min: float
    expected: datetime


class Weather(BaseModel):
    """Current weather snapshot from Open-Meteo (R8)."""

    temperature_c: float
    condition: str  # human-readable summary
    weather_code: int
    wind_kph: float
    precipitation_mm: float


class Prediction(BaseModel):
    """A weather- and delay-adjusted travel-time prediction (R5, R6)."""

    expected_travel_min: float
    scheduled_travel_min: float
    adjustment_min: float  # expected - scheduled
    weather_used: bool  # false when weather was missing (R5.5)
    available: bool  # false only when transit inputs missing (R5.6)


class TimelineNode(BaseModel):
    """A node in the vertical arrival timeline (R7)."""

    stop_id: str
    name: str
    stop_sequence: int
    expected_arrival: datetime
    is_current: bool


T = TypeVar("T")


class ResponseEnvelope(BaseModel, Generic[T]):
    """Shared response envelope wrapping ``data`` with a ``data_status``.

    Every REST endpoint returns this shape so the frontend can uniformly read
    the data provenance (Requirement 10.1)::

        {
          "data": { ... },
          "data_status": { "source": "realtime|mock", "fetched_at": "...",
                           "stale": false }
        }
    """

    data: T
    data_status: DataStatus


def make_data_status(
    source: Literal["realtime", "mock"],
    *,
    fetched_at: Optional[datetime] = None,
    stale: bool = False,
) -> DataStatus:
    """Build a :class:`DataStatus`, defaulting ``fetched_at`` to now (UTC)."""
    return DataStatus(
        source=source,
        fetched_at=fetched_at or datetime.now(timezone.utc),
        stale=stale,
    )


def envelope(
    data: Any,
    *,
    source: Literal["realtime", "mock"] = "mock",
    fetched_at: Optional[datetime] = None,
    stale: bool = False,
    data_status: Optional[DataStatus] = None,
) -> dict:
    """Wrap ``data`` in the shared response envelope.

    Pass either an already-built ``data_status`` or the individual
    ``source`` / ``fetched_at`` / ``stale`` fields. Returns a plain ``dict``
    ready to be serialized by FastAPI.
    """
    status = data_status or make_data_status(
        source, fetched_at=fetched_at, stale=stale
    )
    return {"data": data, "data_status": status}


__all__ = [
    "DataStatus",
    "Mode",
    "Line",
    "Stop",
    "Direction",
    "LineDirections",
    "RouteDelay",
    "ProblemScore",
    "Departure",
    "Weather",
    "Prediction",
    "TimelineNode",
    "ResponseEnvelope",
    "make_data_status",
    "envelope",
]
