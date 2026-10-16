"""Unit tests for the shared data models and response envelope helper."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.models import (
    DataStatus,
    Departure,
    Line,
    Mode,
    Prediction,
    ProblemScore,
    ResponseEnvelope,
    RouteDelay,
    Stop,
    TimelineNode,
    Weather,
    envelope,
    make_data_status,
)


def test_mode_enum_values():
    assert Mode.BUS.value == "bus"
    assert Mode.TRAM.value == "tram"
    assert Mode.TROLLEYBUS.value == "trolleybus"
    assert Mode.METRO.value == "metro"


def test_line_model_roundtrip():
    line = Line(route_id="1234", short_name="4", mode=Mode.TRAM, color="#FFD800")
    dumped = line.model_dump()
    assert dumped["mode"] == Mode.TRAM
    assert Line(**{**dumped, "mode": "tram"}) == line


def test_data_status_fields():
    now = datetime.now(timezone.utc)
    status = DataStatus(source="realtime", fetched_at=now, stale=False)
    assert status.source == "realtime"
    assert status.stale is False


def test_data_status_rejects_invalid_source():
    with pytest.raises(Exception):
        DataStatus(source="paid", fetched_at=datetime.now(timezone.utc), stale=False)


def test_make_data_status_defaults_to_now():
    status = make_data_status("mock")
    assert status.source == "mock"
    assert isinstance(status.fetched_at, datetime)
    assert status.stale is False


def test_envelope_wraps_data_with_status():
    lines = [Line(route_id="M1", short_name="M1", mode=Mode.METRO, color="#FFD800")]
    env = envelope(lines, source="realtime")
    assert env["data"] is lines
    assert env["data_status"].source == "realtime"


def test_envelope_accepts_prebuilt_status():
    status = make_data_status("mock", stale=True)
    env = envelope({"x": 1}, data_status=status)
    assert env["data_status"] is status
    assert env["data_status"].stale is True


def test_response_envelope_generic_serializes():
    status = make_data_status("realtime")
    stop = Stop(stop_id="S1", name="Deak ter", lat=47.4979, lon=19.0402)
    model = ResponseEnvelope[list[Stop]](data=[stop], data_status=status)
    dumped = model.model_dump()
    assert dumped["data"][0]["stop_id"] == "S1"
    assert dumped["data_status"]["source"] == "realtime"


def test_prediction_available_independent_of_weather():
    pred = Prediction(
        expected_travel_min=12.0,
        scheduled_travel_min=10.0,
        adjustment_min=2.0,
        weather_used=False,
        available=True,
    )
    assert pred.available is True
    assert pred.weather_used is False


def test_remaining_models_construct():
    RouteDelay(
        route_id="7", short_name="7", mode=Mode.BUS,
        avg_delay_min=3.5, max_delay_min=9.0,
    )
    ProblemScore(
        route_id="7", short_name="7", mode=Mode.BUS,
        score=0.8, avg_delay_min=3.5,
    )
    Departure(
        trip_id="t1",
        scheduled=datetime.now(timezone.utc),
        delay_min=2.0,
        expected=datetime.now(timezone.utc),
    )
    Weather(
        temperature_c=18.5, condition="Partly cloudy",
        weather_code=2, wind_kph=12.0, precipitation_mm=0.0,
    )
    TimelineNode(
        stop_id="S1", name="Deak ter", stop_sequence=0,
        expected_arrival=datetime.now(timezone.utc), is_current=True,
    )
