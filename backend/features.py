"""Feature engineering for the travel-time prediction model.

:class:`FeatureBuilder` turns a ``(route_id, stop_id, weather, realtime)`` tuple
into the single-row :class:`pandas.DataFrame` the :class:`TravelTimeModel`
consumes. The columns mirror the design's feature list exactly:

    scheduled segment duration, hour-of-day, day-of-week, route mode,
    recent observed delay on the route, and weather features
    (temperature, precipitation, wind, weather-code category).

Design principle: **weather-adjusted but not weather-dependent.** When the
``weather`` argument is ``None`` the weather columns are filled with ``NaN``
(numeric) / ``"unknown"`` (categorical) so an imputation-friendly model still
produces a prediction (Requirements 5.5, 6.4). The feature frame is always
built; missing weather never removes columns or raises.

The set of feature columns is stable and exposed via
:data:`FeatureBuilder.FEATURE_COLUMNS` so training and inference agree on the
schema regardless of whether weather is present.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
import pandas as pd

from backend.models import Mode, Weather
from backend.realtime_service import RealtimeSnapshot

# Numeric feature columns (imputed with the median at inference when missing).
NUMERIC_FEATURES: List[str] = [
    "scheduled_segment_min",
    "hour_of_day",
    "day_of_week",
    "recent_delay_min",
    "temperature_c",
    "precipitation_mm",
    "wind_kph",
]

# Categorical feature columns (imputed with a sentinel category when missing).
CATEGORICAL_FEATURES: List[str] = [
    "route_mode",
    "weather_category",
]

# Sentinel used for a missing categorical value (route mode / weather bucket).
UNKNOWN_CATEGORY = "unknown"

# A sensible default scheduled segment duration (minutes) when the caller does
# not supply one. Kept as a module constant so training and inference agree.
DEFAULT_SEGMENT_MIN = 2.0


def _weather_category(weather_code: int) -> str:
    """Bucket a WMO ``weather_code`` into a coarse category.

    Grouping the ~30 WMO codes into a handful of buckets keeps the categorical
    feature low-cardinality, which helps a tree model generalize.
    """
    code = int(weather_code)
    if code == 0:
        return "clear"
    if code in (1, 2, 3):
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57):
        return "drizzle"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "thunderstorm"
    return UNKNOWN_CATEGORY


class FeatureBuilder:
    """Builds the model feature frame from transit + weather inputs.

    The builder is stateless; a single instance can be reused across requests
    and shared between training and inference.
    """

    #: The full, ordered list of feature columns the model is trained on.
    FEATURE_COLUMNS: List[str] = NUMERIC_FEATURES + CATEGORICAL_FEATURES

    def build(
        self,
        route_id: str,
        stop_id: str,
        weather: Optional[Weather],
        realtime: Optional[RealtimeSnapshot],
        *,
        mode: Optional[Mode] = None,
        scheduled_segment_min: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """Return a single-row feature frame for the given route/stop.

        Parameters
        ----------
        route_id, stop_id:
            The selected route/stop the prediction is for.
        weather:
            Current weather, or ``None`` when it could not be fetched. When
            ``None`` the weather columns are ``NaN`` / ``"unknown"`` so the
            model imputes them (R5.5, R6.4) rather than the caller failing.
        realtime:
            Latest GTFS-RT snapshot used to derive the recent observed delay on
            ``route_id``. ``None`` or an empty snapshot yields a ``0.0`` recent
            delay (no evidence of delay).
        mode:
            The route's transit mode, if known, used as a categorical feature.
        scheduled_segment_min:
            The scheduled duration (minutes) of the segment being predicted.
            Falls back to :data:`DEFAULT_SEGMENT_MIN` when not supplied.
        now:
            Injectable clock for deterministic hour/day features; defaults to
            the current UTC time.
        """
        now = now or datetime.now(timezone.utc)

        recent_delay_min = self._recent_delay_min(route_id, realtime)
        segment_min = (
            float(scheduled_segment_min)
            if scheduled_segment_min is not None
            else DEFAULT_SEGMENT_MIN
        )

        row: dict[str, object] = {
            "scheduled_segment_min": segment_min,
            "hour_of_day": float(now.hour),
            "day_of_week": float(now.weekday()),
            "recent_delay_min": recent_delay_min,
            "route_mode": mode.value if isinstance(mode, Mode) else (
                str(mode) if mode else UNKNOWN_CATEGORY
            ),
        }

        # Weather features: present -> real values; missing -> NaN / "unknown"
        # so the imputation-friendly model still predicts (R5.5, R6.4).
        if weather is not None:
            row["temperature_c"] = float(weather.temperature_c)
            row["precipitation_mm"] = float(weather.precipitation_mm)
            row["wind_kph"] = float(weather.wind_kph)
            row["weather_category"] = _weather_category(weather.weather_code)
        else:
            row["temperature_c"] = np.nan
            row["precipitation_mm"] = np.nan
            row["wind_kph"] = np.nan
            row["weather_category"] = UNKNOWN_CATEGORY

        frame = pd.DataFrame([row], columns=self.FEATURE_COLUMNS)
        # Ensure stable dtypes: numeric columns float, categoricals as object.
        for col in NUMERIC_FEATURES:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        for col in CATEGORICAL_FEATURES:
            frame[col] = frame[col].astype("object")
        return frame

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _recent_delay_min(
        route_id: str, realtime: Optional[RealtimeSnapshot]
    ) -> float:
        """Mean observed delay (minutes) for ``route_id`` in the snapshot.

        Returns ``0.0`` when there is no snapshot or no delay observation for
        the route (absence of evidence -> assume on-time, not missing).
        """
        if realtime is None:
            return 0.0
        delays_sec = [
            d.delay_seconds
            for d in realtime.stop_time_delays
            if d.route_id == route_id and d.delay_seconds is not None
        ]
        if not delays_sec:
            return 0.0
        return float(np.mean(delays_sec)) / 60.0


__all__ = [
    "FeatureBuilder",
    "NUMERIC_FEATURES",
    "CATEGORICAL_FEATURES",
    "UNKNOWN_CATEGORY",
    "DEFAULT_SEGMENT_MIN",
]
