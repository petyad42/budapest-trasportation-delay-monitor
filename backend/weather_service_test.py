"""Unit tests for the Open-Meteo WeatherService.

These tests mock ``httpx`` entirely so no real network call is made. They cover
the successful-parse path (R8.1, R8.2, R6.1), the failure-to-None path (R8.3),
the weather-code -> condition mapping, and the TTL cache behavior.
"""
from __future__ import annotations

import httpx
import pytest

from backend.models import Weather
from backend.weather_service import (
    WeatherService,
    condition_for_code,
)


def _make_payload(
    temperature=18.5, weather_code=2, wind=12.0, precipitation=0.0
) -> dict:
    return {
        "current": {
            "temperature_2m": temperature,
            "weather_code": weather_code,
            "wind_speed_10m": wind,
            "precipitation": precipitation,
        }
    }


class _FakeResponse:
    """Minimal stand-in for ``httpx.Response``."""

    def __init__(self, payload=None, status_code=200, raise_exc=None):
        self._payload = payload
        self.status_code = status_code
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self):
        return self._payload


class _FakeClient:
    """Stand-in ``httpx.Client`` that records calls and returns a fixed result."""

    def __init__(self, response=None, raise_exc=None):
        self._response = response
        self._raise_exc = raise_exc
        self.calls = 0
        self.last_params = None

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        self.last_params = params
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._response


# --- successful parse -------------------------------------------------------


def test_current_parses_successful_response():
    client = _FakeClient(response=_FakeResponse(_make_payload()))
    service = WeatherService(client=client)

    weather = service.current(47.5, 19.05)

    assert isinstance(weather, Weather)
    assert weather.temperature_c == 18.5
    assert weather.weather_code == 2
    assert weather.condition == "Partly cloudy"
    assert weather.wind_kph == 12.0
    assert weather.precipitation_mm == 0.0
    assert client.calls == 1


def test_current_defaults_to_budapest_coordinates():
    client = _FakeClient(response=_FakeResponse(_make_payload(temperature=5.0)))
    service = WeatherService(client=client)

    weather = service.current()

    assert weather is not None
    assert weather.temperature_c == 5.0
    # Params should carry latitude/longitude from config defaults.
    assert "latitude" in client.last_params
    assert "longitude" in client.last_params


def test_current_uses_module_level_httpx_when_no_client(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(_make_payload(temperature=10.0, weather_code=0))

    monkeypatch.setattr(httpx, "get", fake_get)
    service = WeatherService()

    weather = service.current(47.5, 19.05)

    assert weather is not None
    assert weather.temperature_c == 10.0
    assert weather.condition == "Clear sky"
    assert captured["url"] == service.base_url


# --- failure to None --------------------------------------------------------


def test_current_returns_none_on_network_error():
    client = _FakeClient(raise_exc=httpx.ConnectError("boom"))
    service = WeatherService(client=client)

    assert service.current(47.5, 19.05) is None


def test_current_returns_none_on_timeout():
    client = _FakeClient(raise_exc=httpx.TimeoutException("slow"))
    service = WeatherService(client=client)

    assert service.current(47.5, 19.05) is None


def test_current_returns_none_on_http_error_status():
    response = _FakeResponse(
        raise_exc=httpx.HTTPStatusError("500", request=None, response=None)
    )
    client = _FakeClient(response=response)
    service = WeatherService(client=client)

    assert service.current(47.5, 19.05) is None


def test_current_returns_none_when_current_block_missing():
    client = _FakeClient(response=_FakeResponse({"hourly": {}}))
    service = WeatherService(client=client)

    assert service.current(47.5, 19.05) is None


def test_current_does_not_raise_on_failure():
    client = _FakeClient(raise_exc=RuntimeError("unexpected"))
    service = WeatherService(client=client)

    # Must degrade to None, never propagate the exception (R8.3).
    result = service.current(47.5, 19.05)
    assert result is None


# --- weather code mapping ---------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        (0, "Clear sky"),
        (2, "Partly cloudy"),
        (61, "Slight rain"),
        (95, "Thunderstorm"),
        (12345, "Unknown"),
    ],
)
def test_condition_for_code(code, expected):
    assert condition_for_code(code) == expected


# --- TTL cache --------------------------------------------------------------


def test_current_caches_within_ttl():
    client = _FakeClient(response=_FakeResponse(_make_payload()))
    service = WeatherService(client=client, ttl_seconds=1000)

    first = service.current(47.5, 19.05)
    second = service.current(47.5, 19.05)

    assert first == second
    # Second call served from cache -> only one network call.
    assert client.calls == 1


def test_cache_expires_after_ttl():
    client = _FakeClient(response=_FakeResponse(_make_payload()))
    service = WeatherService(client=client, ttl_seconds=0)

    service.current(47.5, 19.05)
    service.current(47.5, 19.05)

    # TTL of 0 means the entry is immediately expired -> refetch each time.
    assert client.calls == 2


def test_failure_is_not_cached():
    client = _FakeClient(raise_exc=httpx.ConnectError("boom"))
    service = WeatherService(client=client, ttl_seconds=1000)

    assert service.current(47.5, 19.05) is None
    assert service.current(47.5, 19.05) is None
    # Both calls attempt the network since failures are never cached.
    assert client.calls == 2
