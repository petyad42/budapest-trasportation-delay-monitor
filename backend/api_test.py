"""FastAPI TestClient tests for the REST API layer (task 13).

These exercise every documented endpoint through the actual FastAPI app,
asserting:

* the shared ``data_status`` envelope is attached to *every* response with the
  ``source`` / ``fetched_at`` / ``stale`` fields (R10.1, Property 4);
* the payload *shape* of each endpoint matches the design's models; and
* the **mock fallback** paths return HTTP 200 with meaningful degraded content
  (R1.3, R9.3, R10.2/10.3), while a **fresh realtime** snapshot flips the
  envelope's ``source`` to ``realtime`` (Property 4).

The whole suite runs offline: the app's dependency container is overridden with
services built from an in-memory GTFS fixture, a hand-seeded realtime service
(no background polling, no network), and a stub weather client. This keeps the
tests deterministic and free of any external call (Property 6).
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from backend import api
from backend.api import Services, get_services
from backend.delay_computer import DelayComputer  # noqa: F401 (documents wiring)
from backend.departure_service import DepartureService
from backend.gtfs_loader import GtfsLoader
from backend.gtfs_repository import GtfsRepository
from backend.main import app
from backend.mock_data import MockDataProvider
from backend.models import Weather
from backend.prediction_service import PredictionService
from backend.realtime_service import (
    RealtimeService,
    RealtimeSnapshot,
    StopTimeDelay,
)
from backend.weather_service import WeatherService


# --- In-memory GTFS fixture --------------------------------------------------
_ROUTES = (
    "route_id,route_short_name,route_type,route_color\n"
    "R_TRAM,4,0,FFD800\n"
    "R_BUS,7,3,009EE3\n"
)
_TRIPS = (
    "trip_id,route_id,service_id,direction_id,shape_id\n"
    "T_TRAM,R_TRAM,WD,0,S1\n"
    "T_BUS,R_BUS,WD,0,S2\n"
)
_STOPS = (
    "stop_id,stop_name,stop_lat,stop_lon\n"
    "STA,Alpha,47.5000,19.0500\n"
    "STB,Bravo,47.5010,19.0510\n"
    "STC,Charlie,47.5020,19.0520\n"
    "STD,Delta,47.5030,19.0530\n"
    "STX,Xray,47.6000,19.1500\n"
)
_STOP_TIMES = (
    "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
    "T_TRAM,STA,08:00:00,08:00:00,1\n"
    "T_TRAM,STB,08:03:00,08:03:00,2\n"
    "T_TRAM,STC,08:06:00,08:06:00,3\n"
    "T_TRAM,STD,08:10:00,08:10:00,4\n"
    "T_BUS,STX,09:00:00,09:00:00,1\n"
)
_CALENDAR = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\n"
    "WD,1,1,1,1,1,0,0,20240101,20241231\n"
)


def _build_zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _fixture_repo() -> GtfsRepository:
    members = {
        "routes.txt": _ROUTES,
        "trips.txt": _TRIPS,
        "stops.txt": _STOPS,
        "stop_times.txt": _STOP_TIMES,
        "calendar.txt": _CALENDAR,
    }
    tables = GtfsLoader().load_bytes(_build_zip(members), source="<fixture>")
    return GtfsRepository(tables)


class _StubWeather(WeatherService):
    """WeatherService double returning a fixed value (or None) with no network."""

    def __init__(self, weather):
        # Skip the real __init__ so no client/config is touched.
        self._weather = weather

    def current(self, lat=None, lon=None):  # type: ignore[override]
        return self._weather


def _weather() -> Weather:
    return Weather(
        temperature_c=12.0,
        condition="Partly cloudy",
        weather_code=2,
        wind_kph=8.0,
        precipitation_mm=0.0,
    )


def _seed_realtime(service: RealtimeService, snapshot) -> None:
    """Inject a snapshot + poll-ok flag so ``status`` reports realtime/mock.

    Bypasses the network entirely; setting ``snapshot=None`` leaves the service
    reporting ``mock`` (nothing fetched yet).
    """
    service._snapshot = snapshot  # noqa: SLF001 - test seam
    service._last_poll_ok = snapshot is not None  # noqa: SLF001


def _fresh_snapshot() -> RealtimeSnapshot:
    """A fresh snapshot carrying tram delays so DelayComputer emits rows."""
    return RealtimeSnapshot(
        fetched_at=datetime.now(timezone.utc),
        stop_time_delays=[
            StopTimeDelay("T_TRAM", "R_TRAM", "STB", 2, 240),
            StopTimeDelay("T_TRAM", "R_TRAM", "STC", 3, 360),
        ],
    )


def _make_services(*, snapshot, weather) -> Services:
    repo = _fixture_repo()
    mock = MockDataProvider()
    realtime = RealtimeService()
    _seed_realtime(realtime, snapshot)
    weather_service = _StubWeather(weather)
    departures = DepartureService(repo, mock=mock)
    predictions = PredictionService(
        repo, weather_service=weather_service, mock=mock
    )
    return Services(
        repository=repo,
        realtime=realtime,
        weather=weather_service,
        departures=departures,
        predictions=predictions,
        mock=mock,
    )


@pytest.fixture()
def client_realtime() -> TestClient:
    """Client whose services have a fresh realtime snapshot and live weather."""
    services = _make_services(snapshot=_fresh_snapshot(), weather=_weather())
    app.dependency_overrides[get_services] = lambda: services
    # Avoid starting the background poller during TestClient lifespan.
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture()
def client_mock() -> TestClient:
    """Client with no realtime snapshot and failed weather -> mock everywhere."""
    services = _make_services(snapshot=None, weather=None)
    app.dependency_overrides[get_services] = lambda: services
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# --- Envelope helper ---------------------------------------------------------
def _assert_envelope(body: dict, *, source: str | None = None) -> dict:
    """Assert the shared envelope shape and return the ``data_status`` block."""
    assert set(body.keys()) == {"data", "data_status"}
    status = body["data_status"]
    assert set(status.keys()) == {"source", "fetched_at", "stale"}
    assert status["source"] in ("realtime", "mock")
    assert isinstance(status["stale"], bool)
    assert isinstance(status["fetched_at"], str) and status["fetched_at"]
    if source is not None:
        assert status["source"] == source
    return status


# --------------------------------------------------------------------------- #
# Envelope presence on every endpoint (Property 4 / R10.1)
# --------------------------------------------------------------------------- #
ENDPOINTS = [
    "/api/status",
    "/api/weather",
    "/api/lines",
    "/api/lines/R_TRAM/stops",
    "/api/lines/R_TRAM/shape",
    "/api/stops/near?lat=47.5&lon=19.05&radius=800",
    "/api/delays",
    "/api/problematic-routes",
    "/api/departures?route_id=R_TRAM&stop_id=STB",
    "/api/predict?route_id=R_TRAM&stop_id=STB",
    "/api/timeline?route_id=R_TRAM&stop_id=STB",
]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_endpoint_returns_envelope(client_realtime: TestClient, path: str):
    resp = client_realtime.get(path)
    assert resp.status_code == 200, resp.text
    _assert_envelope(resp.json())


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_endpoint_ok_in_mock_mode(client_mock: TestClient, path: str):
    """Mock fallback paths still return HTTP 200 with a valid envelope (R10.3)."""
    resp = client_mock.get(path)
    assert resp.status_code == 200, resp.text
    _assert_envelope(resp.json())


# --------------------------------------------------------------------------- #
# /api/status
# --------------------------------------------------------------------------- #
def test_status_reports_realtime_when_snapshot_fresh(client_realtime: TestClient):
    body = client_realtime.get("/api/status").json()
    status = _assert_envelope(body, source="realtime")
    assert body["data"]["realtime"]["source"] == "realtime"
    # Catalog is served from the real fixture GTFS, not mock.
    assert body["data"]["catalog"]["source"] == "realtime"
    assert status["stale"] is False


def test_status_reports_mock_without_snapshot(client_mock: TestClient):
    body = client_mock.get("/api/status").json()
    _assert_envelope(body, source="mock")
    assert body["data"]["realtime"]["source"] == "mock"


# --------------------------------------------------------------------------- #
# /api/weather
# --------------------------------------------------------------------------- #
def test_weather_shape_and_source(client_realtime: TestClient):
    body = client_realtime.get("/api/weather").json()
    _assert_envelope(body, source="realtime")
    data = body["data"]
    assert data["temperature_c"] == 12.0
    assert data["condition"] == "Partly cloudy"
    assert set(data.keys()) == {
        "temperature_c",
        "condition",
        "weather_code",
        "wind_kph",
        "precipitation_mm",
    }


def test_weather_degrades_to_null_and_mock(client_mock: TestClient):
    body = client_mock.get("/api/weather").json()
    _assert_envelope(body, source="mock")
    assert body["data"] is None


# --------------------------------------------------------------------------- #
# /api/lines, stops, shape
# --------------------------------------------------------------------------- #
def test_lines_shape(client_realtime: TestClient):
    body = client_realtime.get("/api/lines").json()
    _assert_envelope(body)
    lines = body["data"]
    assert {l["route_id"] for l in lines} == {"R_TRAM", "R_BUS"}
    tram = next(l for l in lines if l["route_id"] == "R_TRAM")
    assert set(tram.keys()) == {"route_id", "short_name", "mode", "color"}
    assert tram["mode"] == "tram"


def test_line_stops_scoped_to_route(client_realtime: TestClient):
    """Property 2: only the requested route's active-direction stops returned."""
    body = client_realtime.get("/api/lines/R_TRAM/stops").json()
    _assert_envelope(body)
    data = body["data"]
    ids = {s["stop_id"] for s in data["stops"]}
    assert ids == {"STA", "STB", "STC", "STD"}
    assert "STX" not in ids  # STX belongs to R_BUS only
    # Direction metadata is exposed with start/end termini (R4.3).
    assert data["directions"], "expected at least one direction"
    active = next(
        d for d in data["directions"] if d["direction_id"] == data["active_direction"]
    )
    assert active["start_terminus"] == data["stops"][0]["name"]
    assert active["end_terminus"] == data["stops"][-1]["name"]


def test_line_shape_is_latlon_pairs(client_realtime: TestClient):
    body = client_realtime.get("/api/lines/R_TRAM/shape").json()
    _assert_envelope(body)
    shape = body["data"]
    assert len(shape) == 4
    assert all(len(pair) == 2 for pair in shape)
    assert shape[0] == [47.5000, 19.0500]


def test_stops_near_sorted_within_radius(client_realtime: TestClient):
    body = client_realtime.get(
        "/api/stops/near?lat=47.5000&lon=19.0500&radius=800"
    ).json()
    _assert_envelope(body)
    ids = [s["stop_id"] for s in body["data"]]
    assert ids and ids[0] == "STA"  # nearest first
    assert "STX" not in ids  # far away, outside the radius


def test_stops_near_requires_coordinates(client_realtime: TestClient):
    # Missing required lat/lon -> FastAPI validation error (not a 200 envelope).
    resp = client_realtime.get("/api/stops/near?lat=47.5")
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# /api/delays and /api/problematic-routes
# --------------------------------------------------------------------------- #
def test_delays_realtime_from_snapshot(client_realtime: TestClient):
    body = client_realtime.get("/api/delays").json()
    _assert_envelope(body, source="realtime")
    rows = body["data"]
    tram = next(r for r in rows if r["route_id"] == "R_TRAM")
    assert set(tram.keys()) == {
        "route_id",
        "short_name",
        "mode",
        "avg_delay_min",
        "max_delay_min",
    }
    assert tram["max_delay_min"] == pytest.approx(6.0)  # 360s


def test_delays_mock_fallback(client_mock: TestClient):
    body = client_mock.get("/api/delays").json()
    _assert_envelope(body, source="mock")
    assert isinstance(body["data"], list) and body["data"]


def test_problematic_routes_limit_and_order(client_mock: TestClient):
    body = client_mock.get("/api/problematic-routes?limit=3").json()
    _assert_envelope(body, source="mock")
    rows = body["data"]
    assert len(rows) <= 3
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)  # Property 3: descending


# --------------------------------------------------------------------------- #
# /api/departures
# --------------------------------------------------------------------------- #
def test_departures_shape(client_realtime: TestClient):
    body = client_realtime.get(
        "/api/departures?route_id=R_TRAM&stop_id=STB&limit=5"
    ).json()
    _assert_envelope(body)
    for dep in body["data"]:
        assert set(dep.keys()) == {"trip_id", "scheduled", "delay_min", "expected"}


def test_departures_mock_fallback(client_mock: TestClient):
    body = client_mock.get(
        "/api/departures?route_id=R_TRAM&stop_id=STB"
    ).json()
    _assert_envelope(body, source="mock")


def test_departures_requires_params(client_realtime: TestClient):
    resp = client_realtime.get("/api/departures?route_id=R_TRAM")
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# /api/predict (Property 1: weather-independent availability)
# --------------------------------------------------------------------------- #
def test_predict_shape_with_weather(client_realtime: TestClient):
    body = client_realtime.get(
        "/api/predict?route_id=R_TRAM&stop_id=STB"
    ).json()
    _assert_envelope(body)
    data = body["data"]
    assert set(data.keys()) == {
        "expected_travel_min",
        "scheduled_travel_min",
        "adjustment_min",
        "weather_used",
        "available",
    }
    assert data["available"] is True
    assert data["weather_used"] is True


def test_predict_available_without_weather(client_mock: TestClient):
    """Property 1: prediction stays available even when weather is missing."""
    body = client_mock.get(
        "/api/predict?route_id=R_TRAM&stop_id=STB"
    ).json()
    _assert_envelope(body)
    data = body["data"]
    assert data["available"] is True
    assert data["weather_used"] is False


# --------------------------------------------------------------------------- #
# /api/timeline (Property 5: head invariant)
# --------------------------------------------------------------------------- #
def test_timeline_head_invariant(client_realtime: TestClient):
    body = client_realtime.get(
        "/api/timeline?route_id=R_TRAM&stop_id=STB"
    ).json()
    _assert_envelope(body)
    nodes = body["data"]
    assert nodes[0]["is_current"] is True
    assert nodes[0]["stop_id"] == "STB"
    sequences = [n["stop_sequence"] for n in nodes]
    assert sequences == sorted(sequences)
    assert sequences[0] == 0


def test_get_services_is_cached():
    """The process-wide container is a singleton (built once)."""
    api.get_services.cache_clear()
    first = api.get_services()
    second = api.get_services()
    assert first is second
    api.get_services.cache_clear()
