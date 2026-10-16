"""Open-Meteo weather client with a TTL cache and graceful failure.

The :class:`WeatherService` fetches the *current* weather (temperature, weather
code, wind, precipitation) for a location from the free Open-Meteo API (no API
key required, Requirement 6.1 / 9.5). Results are cached for a short TTL to
avoid hammering the endpoint (design: "short TTL cache").

Design principle: **degrade, do not fail.** Any network, timeout, or parsing
error results in :meth:`WeatherService.current` returning ``None`` instead of
raising, so callers (the weather widget and the prediction model) can proceed
with a graceful fallback / imputed features (Requirements 8.3, 6.4).
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

import httpx

from backend import config
from backend.models import Weather

logger = logging.getLogger(__name__)

# Default cache lifetime for a fetched weather snapshot, in seconds.
DEFAULT_TTL_SECONDS = 300.0

# WMO weather interpretation codes -> human-readable condition summary (R8.2).
# https://open-meteo.com/en/docs (WMO Weather interpretation codes)
_WEATHER_CODE_CONDITIONS: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def condition_for_code(weather_code: int) -> str:
    """Map a WMO ``weather_code`` to a human-readable condition (R8.2)."""
    return _WEATHER_CODE_CONDITIONS.get(int(weather_code), "Unknown")


class WeatherService:
    """Fetches current weather from Open-Meteo with a TTL cache.

    Parameters
    ----------
    base_url:
        The Open-Meteo forecast endpoint. Defaults to :data:`config.OPEN_METEO_URL`.
    timeout:
        Per-request timeout in seconds. Defaults to :data:`config.HTTP_TIMEOUT_SECONDS`.
    ttl_seconds:
        How long a fetched snapshot stays cached before a refetch.
    client:
        Optional pre-built ``httpx.Client`` (useful for tests / connection reuse).
        When omitted, a short-lived client is created per request.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = base_url or config.OPEN_METEO_URL
        self.timeout = timeout if timeout is not None else config.HTTP_TIMEOUT_SECONDS
        self.ttl_seconds = ttl_seconds
        self._client = client
        # Cache maps (rounded lat, rounded lon) -> (expires_at_monotonic, Weather).
        self._cache: dict[Tuple[float, float], Tuple[float, Weather]] = {}

    # -- public API ---------------------------------------------------------

    def current(
        self, lat: Optional[float] = None, lon: Optional[float] = None
    ) -> Optional[Weather]:
        """Return current weather for ``(lat, lon)`` or ``None`` on failure.

        Defaults to the Budapest coordinates from config. Never raises: any
        network/timeout/parse error is logged and yields ``None`` so the caller
        degrades gracefully (Requirements 8.3, 6.4).
        """
        lat = config.CITY_LAT if lat is None else lat
        lon = config.CITY_LON if lon is None else lon

        cached = self._get_cached(lat, lon)
        if cached is not None:
            return cached

        try:
            payload = self._fetch(lat, lon)
            weather = self._parse(payload)
        except Exception as exc:  # noqa: BLE001 - degrade, do not fail (R8.3).
            logger.warning("Open-Meteo weather fetch failed for (%s, %s): %s", lat, lon, exc)
            return None

        if weather is not None:
            self._store(lat, lon, weather)
        return weather

    # -- internals ----------------------------------------------------------

    def _fetch(self, lat: float, lon: float) -> dict:
        """Perform the HTTP request to Open-Meteo and return parsed JSON."""
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": (
                "temperature_2m,weather_code,wind_speed_10m,precipitation"
            ),
            "wind_speed_unit": "kmh",
        }
        if self._client is not None:
            response = self._client.get(self.base_url, params=params, timeout=self.timeout)
        else:
            response = httpx.get(self.base_url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _parse(self, payload: dict) -> Optional[Weather]:
        """Parse an Open-Meteo ``current`` payload into a :class:`Weather`.

        Returns ``None`` if the expected ``current`` block is missing.
        """
        current = payload.get("current")
        if not isinstance(current, dict):
            logger.warning("Open-Meteo response missing 'current' block: %s", payload)
            return None

        weather_code = int(current.get("weather_code", 0) or 0)
        return Weather(
            temperature_c=float(current.get("temperature_2m", 0.0) or 0.0),
            condition=condition_for_code(weather_code),
            weather_code=weather_code,
            wind_kph=float(current.get("wind_speed_10m", 0.0) or 0.0),
            precipitation_mm=float(current.get("precipitation", 0.0) or 0.0),
        )

    # -- cache helpers ------------------------------------------------------

    @staticmethod
    def _cache_key(lat: float, lon: float) -> Tuple[float, float]:
        # Round to ~100m so nearby lookups share a cache entry.
        return (round(lat, 3), round(lon, 3))

    def _get_cached(self, lat: float, lon: float) -> Optional[Weather]:
        entry = self._cache.get(self._cache_key(lat, lon))
        if entry is None:
            return None
        expires_at, weather = entry
        if time.monotonic() >= expires_at:
            self._cache.pop(self._cache_key(lat, lon), None)
            return None
        return weather

    def _store(self, lat: float, lon: float, weather: Weather) -> None:
        self._cache[self._cache_key(lat, lon)] = (
            time.monotonic() + self.ttl_seconds,
            weather,
        )


__all__ = ["WeatherService", "condition_for_code", "DEFAULT_TTL_SECONDS"]
