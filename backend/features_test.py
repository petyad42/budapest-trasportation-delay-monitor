"""Unit tests for FeatureBuilder and TravelTimeModel.

Core guarantee under test: the prediction pipeline is *weather-adjusted but not
weather-dependent*. ``predict`` returns a value both when weather is present and
when weather is ``None`` (Requirements 5.5, 6.4). We also cover the feature
frame schema, the recent-delay derivation from a realtime snapshot, and the
train/predict/save/load lifecycle (R6.2).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from backend.features import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    UNKNOWN_CATEGORY,
    FeatureBuilder,
)
from backend.models import Mode, Weather
from backend.realtime_service import RealtimeSnapshot, StopTimeDelay
from backend.travel_time_model import TravelTimeModel


# --- fixtures / helpers ---------------------------------------------------


def _weather() -> Weather:
    return Weather(
        temperature_c=5.0,
        condition="Slight rain",
        weather_code=61,
        wind_kph=20.0,
        precipitation_mm=1.4,
    )


def _snapshot_with_delay(route_id: str, delay_seconds: int) -> RealtimeSnapshot:
    return RealtimeSnapshot(
        fetched_at=datetime.now(timezone.utc),
        stop_time_delays=[
            StopTimeDelay(
                trip_id="t1",
                route_id=route_id,
                stop_id="s1",
                stop_sequence=1,
                delay_seconds=delay_seconds,
            )
        ],
    )


def _synthetic_training_set(n: int = 400) -> tuple[pd.DataFrame, pd.Series]:
    """Build a small synthetic training set covering weather present + missing.

    The target is a simple deterministic function of the features plus noise so
    the model has a real signal to learn. Roughly a third of rows have missing
    weather so the trained model has seen the imputed regime too.
    """
    rng = np.random.default_rng(0)
    rows = []
    targets = []
    modes = ["bus", "tram", "trolleybus", "metro"]
    categories = ["clear", "cloudy", "rain", "snow", UNKNOWN_CATEGORY]
    for i in range(n):
        segment = float(rng.uniform(1.0, 10.0))
        hour = float(rng.integers(0, 24))
        dow = float(rng.integers(0, 7))
        recent_delay = float(rng.uniform(0.0, 8.0))
        weather_missing = rng.random() < 0.33
        if weather_missing:
            temp = np.nan
            precip = np.nan
            wind = np.nan
            cat = UNKNOWN_CATEGORY
        else:
            temp = float(rng.uniform(-10.0, 35.0))
            precip = float(rng.uniform(0.0, 12.0))
            wind = float(rng.uniform(0.0, 60.0))
            cat = categories[int(rng.integers(0, len(categories)))]
        mode = modes[int(rng.integers(0, len(modes)))]

        # Deterministic-ish target: base travel time scaled by delay + weather.
        weather_penalty = 0.0 if weather_missing else (precip * 0.1 + wind * 0.02)
        target = (
            segment * 1.1
            + recent_delay * 0.5
            + weather_penalty
            + (1.0 if hour in (7, 8, 16, 17) else 0.0)
            + rng.normal(0, 0.3)
        )
        rows.append(
            {
                "scheduled_segment_min": segment,
                "hour_of_day": hour,
                "day_of_week": dow,
                "recent_delay_min": recent_delay,
                "temperature_c": temp,
                "precipitation_mm": precip,
                "wind_kph": wind,
                "route_mode": mode,
                "weather_category": cat,
            }
        )
        targets.append(target)
    X = pd.DataFrame(rows, columns=FeatureBuilder.FEATURE_COLUMNS)
    y = pd.Series(targets, name="travel_min")
    return X, y


@pytest.fixture()
def trained_model() -> TravelTimeModel:
    X, y = _synthetic_training_set()
    model = TravelTimeModel()
    model.train(X, y)
    return model


# --- FeatureBuilder tests -------------------------------------------------


def test_build_produces_full_schema_with_weather():
    builder = FeatureBuilder()
    frame = builder.build(
        route_id="R1",
        stop_id="S1",
        weather=_weather(),
        realtime=_snapshot_with_delay("R1", 120),
        mode=Mode.TRAM,
        scheduled_segment_min=3.0,
        now=datetime(2024, 1, 2, 8, 0, tzinfo=timezone.utc),  # Tuesday 08:00
    )
    assert list(frame.columns) == FeatureBuilder.FEATURE_COLUMNS
    assert len(frame) == 1
    assert frame.loc[0, "scheduled_segment_min"] == 3.0
    assert frame.loc[0, "hour_of_day"] == 8.0
    assert frame.loc[0, "day_of_week"] == 1.0  # Tuesday
    assert frame.loc[0, "route_mode"] == "tram"
    assert frame.loc[0, "weather_category"] == "rain"
    # 120 seconds observed delay -> 2.0 minutes recent delay.
    assert frame.loc[0, "recent_delay_min"] == pytest.approx(2.0)
    assert frame.loc[0, "temperature_c"] == 5.0


def test_build_imputes_when_weather_none():
    builder = FeatureBuilder()
    frame = builder.build(
        route_id="R1",
        stop_id="S1",
        weather=None,
        realtime=None,
        mode=Mode.BUS,
    )
    # Schema is unchanged: weather columns exist but are NaN / "unknown".
    assert list(frame.columns) == FeatureBuilder.FEATURE_COLUMNS
    for col in ("temperature_c", "precipitation_mm", "wind_kph"):
        assert math.isnan(frame.loc[0, col])
    assert frame.loc[0, "weather_category"] == UNKNOWN_CATEGORY
    # No realtime snapshot -> assume on-time (0.0), not missing.
    assert frame.loc[0, "recent_delay_min"] == 0.0


def test_recent_delay_ignores_other_routes():
    builder = FeatureBuilder()
    snap = RealtimeSnapshot(
        fetched_at=datetime.now(timezone.utc),
        stop_time_delays=[
            StopTimeDelay("t1", "R1", "s1", 1, 300),
            StopTimeDelay("t2", "R2", "s2", 1, 600),  # different route
        ],
    )
    frame = builder.build("R1", "S1", weather=None, realtime=snap)
    assert frame.loc[0, "recent_delay_min"] == pytest.approx(5.0)  # 300s only


# --- TravelTimeModel tests ------------------------------------------------


def test_predict_returns_value_with_weather_present(trained_model):
    """R5.5 / R6.4: a prediction is produced when weather is available."""
    builder = FeatureBuilder()
    frame = builder.build(
        "R1", "S1", weather=_weather(), realtime=_snapshot_with_delay("R1", 180),
        mode=Mode.BUS, scheduled_segment_min=4.0,
    )
    value = trained_model.predict(frame)
    assert isinstance(value, float)
    assert math.isfinite(value)


def test_predict_returns_value_with_weather_none(trained_model):
    """R5.5 / R6.4: prediction NEVER requires weather; missing -> still a value."""
    builder = FeatureBuilder()
    frame = builder.build(
        "R1", "S1", weather=None, realtime=_snapshot_with_delay("R1", 180),
        mode=Mode.BUS, scheduled_segment_min=4.0,
    )
    value = trained_model.predict(frame)
    assert isinstance(value, float)
    assert math.isfinite(value)


def test_predict_before_training_raises():
    model = TravelTimeModel()
    builder = FeatureBuilder()
    frame = builder.build("R1", "S1", weather=None, realtime=None)
    with pytest.raises(RuntimeError):
        model.predict(frame)


def test_save_and_load_roundtrip(trained_model, tmp_path):
    """R6.2: the trained pipeline persists and reloads, giving equal predictions."""
    builder = FeatureBuilder()
    frame = builder.build(
        "R1", "S1", weather=None, realtime=_snapshot_with_delay("R1", 60),
        mode=Mode.METRO, scheduled_segment_min=2.5,
    )
    before = trained_model.predict(frame)

    artifact = tmp_path / "delay_model.joblib"
    saved_path = trained_model.save(artifact)
    assert saved_path.exists()

    reloaded = TravelTimeModel(model_path=artifact)
    reloaded.load()
    after = reloaded.predict(frame)
    assert after == pytest.approx(before)


def test_save_untrained_raises(tmp_path):
    model = TravelTimeModel(model_path=tmp_path / "m.joblib")
    with pytest.raises(RuntimeError):
        model.save()


def test_train_missing_columns_raises():
    model = TravelTimeModel()
    bad = pd.DataFrame([{"scheduled_segment_min": 1.0}])
    with pytest.raises(ValueError):
        model.train(bad, pd.Series([1.0]))
