"""REST API layer wiring every endpoint with the shared data_status envelope.

This module (task 13) exposes the documented JSON REST endpoints described in
the design doc's "API layer" section and attaches the shared response envelope
to *every* response::

    {
      "data": { ... },
      "data_status": { "source": "realtime|mock", "fetched_at": "ISO-8601",
                       "stale": false }
    }

Design principles honoured here:

* **Degrade, do not fail (R9.3, R10.2/10.3).** Endpoints return HTTP 200 with a
  degraded payload where a partial answer is meaningful (e.g. ``weather`` may be
  ``null``; delays fall back to mock). Non-200 is reserved for genuine bad
  input (e.g. a missing required query parameter, handled by FastAPI).
* **Data-status truthfulness (Property 4, R1.2/1.3/10.1/10.2).** Realtime-derived
  endpoints report the ``RealtimeService.status`` verbatim; static/catalog
  endpoints report ``realtime`` when served from real GTFS and ``mock`` when the
  repository is mock-backed; weather reports ``realtime`` on a successful
  Open-Meteo fetch and ``mock`` when it degrades to a fallback.

The heavy service objects (GTFS repository, realtime poller, weather, delay,
departure and prediction services) are constructed once and shared through a
small :class:`Services` container. Tests build their own container and override
``get_services`` so they can inject mock/fixture-backed services without any
network access.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend import config
from backend.delay_computer import DelayComputer
from backend.departure_service import DepartureService
from backend.gtfs_repository import GtfsRepository
from backend.mock_data import MockDataProvider
from backend.models import (
    DataStatus,
    Weather,
    envelope,
    make_data_status,
)
from backend.prediction_service import PredictionService
from backend.realtime_service import RealtimeService
from backend.weather_service import WeatherService

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Service container and dependency wiring
# --------------------------------------------------------------------------- #
@dataclass
class Services:
    """Bundle of the long-lived services the routers depend on.

    Constructed once per process (see :func:`get_services`) or built ad-hoc by
    tests to inject mock/fixture-backed services.
    """

    repository: GtfsRepository
    realtime: RealtimeService
    weather: WeatherService
    departures: DepartureService
    predictions: PredictionService
    mock: MockDataProvider

    # -- derived helpers ----------------------------------------------------
    def realtime_status(self) -> DataStatus:
        """The current realtime data-status (source/fetched_at/stale)."""
        return self.realtime.status

    def catalog_status(self) -> DataStatus:
        """Data-status for static catalog reads (lines/stops/shape).

        ``realtime`` when the repository is serving real GTFS, ``mock`` when it
        fell back to the mock catalog (Property 4).
        """
        source = "mock" if self.repository.is_mock else "realtime"
        return make_data_status(source)

    def delay_computer(self) -> DelayComputer:
        """A :class:`DelayComputer` bound to the latest realtime snapshot."""
        return DelayComputer(self.repository, self.realtime.latest_trip_updates())


@lru_cache(maxsize=1)
def get_services() -> Services:
    """Build (once) and return the shared service container.

    Uses :func:`GtfsRepository.from_zip`, which transparently falls back to a
    mock-backed repository if the GTFS zip is missing or corrupt, so the API
    always comes up (R9.3, R10.3). No background polling is started here; the
    app lifespan hook owns that so importing this module has no side effects.
    """
    repository = GtfsRepository.from_zip(config.GTFS_ZIP_PATH)
    mock = MockDataProvider()
    # Pass the static schedule so the poller can derive delays for feeds that
    # report absolute predicted times instead of a GTFS-RT delay field (BKK).
    realtime = RealtimeService(repository=repository)
    weather = WeatherService()
    departures = DepartureService(repository, mock=mock)
    predictions = PredictionService(
        repository, weather_service=weather, mock=mock
    )
    return Services(
        repository=repository,
        realtime=realtime,
        weather=weather,
        departures=departures,
        predictions=predictions,
        mock=mock,
    )


router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
# Status / weather
# --------------------------------------------------------------------------- #
@router.get("/status")
def get_status(services: Services = Depends(get_services)) -> dict:
    """Global data status per source (realtime feed + static catalog).

    The envelope's top-level ``data_status`` mirrors the realtime feed status so
    the frontend badge can read it uniformly (R10.1, R10.2); ``data`` breaks the
    status down per source for richer UIs.
    """
    rt = services.realtime_status()
    catalog = services.catalog_status()
    data = {
        "realtime": rt.model_dump(mode="json"),
        "catalog": catalog.model_dump(mode="json"),
    }
    return envelope(data, data_status=rt)


@router.get("/weather")
def get_weather(
    lat: Optional[float] = Query(default=None),
    lon: Optional[float] = Query(default=None),
    services: Services = Depends(get_services),
) -> dict:
    """Current weather for ``(lat, lon)`` (defaults to Budapest).

    Never fails: on an Open-Meteo error the payload is ``null`` and the status
    degrades to ``mock`` so the widget shows its fallback (R8.3, R10.2).
    """
    weather: Optional[Weather] = services.weather.current(lat, lon)
    if weather is None:
        return envelope(None, source="mock")
    return envelope(weather, source="realtime")


# --------------------------------------------------------------------------- #
# Lines / stops / shapes (static catalog)
# --------------------------------------------------------------------------- #
@router.get("/lines")
def get_lines(services: Services = Depends(get_services)) -> dict:
    """All lines with id, short name, mode and color (R2.3, R4.1)."""
    lines = services.repository.list_lines()
    return envelope(lines, data_status=services.catalog_status())


@router.get("/lines/{route_id}/stops")
def get_line_stops(
    route_id: str,
    direction: Optional[str] = Query(default=None),
    services: Services = Depends(get_services),
) -> dict:
    """Direction-scoped stops + termini for a line (R4.2-R4.4, R4.7, Property 2).

    Returns the active direction's stops ordered start terminus -> end
    terminus with no duplicates, plus every direction the line serves (each
    with its start/end terminus names) so the UI can render and enable the
    direction-flip control. ``direction`` selects the active direction; when
    omitted the lowest available direction id is used.
    """
    result = services.repository.line_directions(route_id, direction)
    return envelope(result, data_status=services.catalog_status())


@router.get("/lines/{route_id}/shape")
def get_line_shape(
    route_id: str, services: Services = Depends(get_services)
) -> dict:
    """Ordered ``[lat, lon]`` polyline for highlighting a line (R2.4)."""
    shape = [[lat, lon] for lat, lon in services.repository.route_shape(route_id)]
    return envelope(shape, data_status=services.catalog_status())


@router.get("/stops/near")
def get_stops_near(
    lat: float = Query(...),
    lon: float = Query(...),
    radius: int = Query(default=500, ge=0),
    services: Services = Depends(get_services),
) -> dict:
    """Nearby stops within ``radius`` metres, nearest-first (R2.1)."""
    stops = services.repository.stops_near(lat, lon, radius)
    return envelope(stops, data_status=services.catalog_status())


# --------------------------------------------------------------------------- #
# Delays / problematic routes (realtime-derived)
# --------------------------------------------------------------------------- #
@router.get("/delays")
def get_delays(services: Services = Depends(get_services)) -> dict:
    """Current per-route delays (R1.1, R1.4).

    When no fresh realtime snapshot exists the computed list is empty; we then
    fall back to deterministic mock delays so the dashboard always has content
    (R1.3, R9.3, R10.3). ``data_status`` reflects the true source used.
    """
    status = services.realtime_status()
    if status.source == "realtime":
        delays = services.delay_computer().route_delays()
        if delays:
            return envelope(delays, data_status=status)
    # No fresh realtime (or it produced nothing) -> mock fallback.
    delays = services.mock.route_delays()
    return envelope(delays, source="mock", stale=status.stale)


@router.get("/problematic-routes")
def get_problematic_routes(
    limit: int = Query(default=5, ge=1, le=50),
    services: Services = Depends(get_services),
) -> dict:
    """Top-N problematic routes by descending problem score (R3, Property 3)."""
    status = services.realtime_status()
    if status.source == "realtime":
        top = services.delay_computer().top_problematic(limit)
        if top:
            return envelope(top, data_status=status)
    top = services.mock.top_problematic(limit)
    return envelope(top, source="mock", stale=status.stale)


# --------------------------------------------------------------------------- #
# Departures / prediction / timeline
# --------------------------------------------------------------------------- #
@router.get("/departures")
def get_departures(
    route_id: str = Query(...),
    stop_id: str = Query(...),
    limit: int = Query(default=500, ge=1, le=1000),
    services: Services = Depends(get_services),
) -> dict:
    """All of the active service day's departures for a stop (R5.1-R5.4).

    Merges the static schedule with the latest realtime delays; the service
    itself falls back to mock when there is no usable schedule.
    """
    status = services.realtime_status()
    snapshot = services.realtime.latest_trip_updates()
    departures = services.departures.upcoming(
        route_id, stop_id, limit=limit, snapshot=snapshot
    )
    # Realtime only when a fresh snapshot actually informed the delays.
    source = "realtime" if status.source == "realtime" else "mock"
    return envelope(departures, source=source, stale=status.stale)


@router.get("/predict")
def get_predict(
    route_id: str = Query(...),
    stop_id: str = Query(...),
    lat: Optional[float] = Query(default=None),
    lon: Optional[float] = Query(default=None),
    services: Services = Depends(get_services),
) -> dict:
    """Weather/delay-adjusted travel-time prediction (R5.3-5.6, R6).

    Never blocked by missing weather (Property 1): ``weather_used=false`` but
    the prediction is still returned. ``available=false`` only when transit
    inputs are unbuildable (R5.6).
    """
    status = services.realtime_status()
    snapshot = services.realtime.latest_trip_updates()
    prediction = services.predictions.predict_travel_time(
        route_id, stop_id, snapshot=snapshot, lat=lat, lon=lon
    )
    source = "realtime" if status.source == "realtime" else "mock"
    return envelope(prediction, source=source, stale=status.stale)


@router.get("/timeline")
def get_timeline(
    route_id: str = Query(...),
    stop_id: str = Query(...),
    trip_id: Optional[str] = Query(default=None),
    lat: Optional[float] = Query(default=None),
    lon: Optional[float] = Query(default=None),
    services: Services = Depends(get_services),
) -> dict:
    """Vertical arrival timeline for the selection (R7, Property 5).

    When ``trip_id`` is supplied (the departure the rider selected) the timeline
    is anchored on that exact trip so every node reflects that run's times; it
    falls back to the next-trip auto-pick when omitted or when the trip does not
    serve the stop.
    """
    status = services.realtime_status()
    snapshot = services.realtime.latest_trip_updates()
    nodes = services.predictions.arrival_timeline(
        route_id, stop_id, trip_id=trip_id, snapshot=snapshot, lat=lat, lon=lon
    )
    source = "realtime" if status.source == "realtime" else "mock"
    return envelope(nodes, source=source, stale=status.stale)


__all__ = ["router", "Services", "get_services"]
