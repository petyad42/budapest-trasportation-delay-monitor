"""Unit tests for :mod:`backend.prediction_service` (task 12).

These pin the two invariants the design calls out for PredictionService:

* **Property 1 — prediction is weather-independent (R5.5, R6.4).** When weather
  is missing (``WeatherService.current`` -> ``None``) the prediction is still
  produced: ``weather_used`` is ``False`` but ``available`` stays ``True``.
  When the transit inputs themselves cannot be built the prediction reports
  ``available=False`` (R5.6).
* **Property 5 — timeline head invariant (R7.1, R7.2).** The first
  ``TimelineNode`` is the selected stop with ``is_current=True`` and
  ``stop_sequence=0``; subsequent nodes follow in increasing ``stop_sequence``.

The model artifact may not exist yet (task 10 runs in parallel), so we also
cover the model-absent heuristic fallback and inject a trained model to cover
the model-present path — neither path may depend on weather being present.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from backend.features import UNKNOWN_CATEGORY, FeatureBuilder
from backend.gtfs_loader import GtfsLoader
from backend.gtfs_repository import GtfsRepository
from backend.models import Weather
from backend.prediction_service import DEFAULT_SEGMENT_MIN, PredictionService
from backend.realtime_service import RealtimeSnapshot, StopTimeDelay
from backend.travel_time_model import TravelTimeModel


# --- Fixture GTFS feed -------------------------------------------------------
#
# Line R_TRAM: STA 08:00 -> STB 08:03 -> STC 08:06 -> STD 08:10
#   segment STB->STC is 3 minutes scheduled.

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

SERVICE_DATE = datetime(2024, 6, 3, 0, 0, 0, tzinfo=timezone.utc)  # a Monday

_BUDAPEST = ZoneInfo("Europe/Budapest")


def _bud(h, mi, *, day=3):
    """Budapest-local wall-clock instant on 2024-06-``day`` (GTFS clock strings
    are Budapest local, so timeline instants are anchored in that zone)."""
    return datetime(2024, 6, day, h, mi, 0, tzinfo=_BUDAPEST)


def _build_zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buf.getvalue()


@pytest.fixture()
def repo() -> GtfsRepository:
    members = {
        "routes.txt": _ROUTES,
        "trips.txt": _TRIPS,
        "stops.txt": _STOPS,
        "stop_times.txt": _STOP_TIMES,
        "calendar.txt": _CALENDAR,
    }
    tables = GtfsLoader().load_bytes(_build_zip(members), source="<fixture>")
    return GtfsRepository(tables)


class _StubWeather:
    """WeatherService double that returns a fixed value (or None)."""

    def __init__(self, weather):
        self._weather = weather
        self.calls = 0

    def current(self, lat=None, lon=None):
        self.calls += 1
        return self._weather


def _weather() -> Weather:
    return Weather(
        temperature_c=5.0,
        condition="Slight rain",
        weather_code=61,
        wind_kph=20.0,
        precipitation_mm=1.4,
    )


def _no_model(tmp_path) -> TravelTimeModel:
    """A model bound to a nonexistent artifact so lazy load fails -> heuristic.

    This makes the heuristic-fallback tests deterministic regardless of whether
    a real ``data/delay_model.joblib`` artifact happens to exist on disk (task
    10 runs in parallel and may or may not have produced one).
    """
    return TravelTimeModel(model_path=tmp_path / "does_not_exist.joblib")


def _trained_model() -> TravelTimeModel:
    """Train a tiny model so the model-present path is exercised."""
    rng = np.random.default_rng(0)
    rows, targets = [], []
    modes = ["bus", "tram", "trolleybus", "metro"]
    cats = ["clear", "cloudy", "rain", "snow", UNKNOWN_CATEGORY]
    for _ in range(200):
        seg = float(rng.uniform(1.0, 10.0))
        missing = rng.random() < 0.33
        rows.append(
            {
                "scheduled_segment_min": seg,
                "hour_of_day": float(rng.integers(0, 24)),
                "day_of_week": float(rng.integers(0, 7)),
                "recent_delay_min": float(rng.uniform(0.0, 8.0)),
                "temperature_c": np.nan if missing else float(rng.uniform(-5, 30)),
                "precipitation_mm": np.nan if missing else float(rng.uniform(0, 10)),
                "wind_kph": np.nan if missing else float(rng.uniform(0, 40)),
                "route_mode": modes[int(rng.integers(0, len(modes)))],
                "weather_category": UNKNOWN_CATEGORY if missing
                else cats[int(rng.integers(0, len(cats)))],
            }
        )
        targets.append(seg * 1.1 + rng.normal(0, 0.2))
    X = pd.DataFrame(rows, columns=FeatureBuilder.FEATURE_COLUMNS)
    model = TravelTimeModel()
    model.train(X, pd.Series(targets))
    return model


# --- Property 1: prediction is weather-independent ---------------------------


def test_prediction_available_when_weather_missing_heuristic(repo, tmp_path):
    """R5.5/R6.4: weather None -> weather_used False but available stays True.

    Model artifact absent (task 10 not yet run) -> heuristic fallback path.
    """
    svc = PredictionService(
        repo, weather_service=_StubWeather(None), model=_no_model(tmp_path)
    )
    pred = svc.predict_travel_time("R_TRAM", "STB", now=SERVICE_DATE)

    assert pred.available is True
    assert pred.weather_used is False
    # Scheduled segment STB->STC is 3 minutes.
    assert pred.scheduled_travel_min == pytest.approx(3.0)
    # Heuristic with no realtime delay -> expected == scheduled.
    assert pred.expected_travel_min == pytest.approx(3.0)
    assert pred.adjustment_min == pytest.approx(0.0)


def test_prediction_available_when_weather_missing_with_model(repo):
    """R5.5/R6.4: even with a trained model, weather None still predicts."""
    svc = PredictionService(
        repo, weather_service=_StubWeather(None), model=_trained_model()
    )
    pred = svc.predict_travel_time("R_TRAM", "STB", now=SERVICE_DATE)

    assert pred.available is True
    assert pred.weather_used is False
    assert np.isfinite(pred.expected_travel_min)


def test_prediction_weather_used_true_when_weather_present(repo):
    svc = PredictionService(
        repo, weather_service=_StubWeather(_weather()), model=_trained_model()
    )
    pred = svc.predict_travel_time("R_TRAM", "STB", now=SERVICE_DATE)

    assert pred.available is True
    assert pred.weather_used is True


def test_prediction_heuristic_applies_recent_delay(repo, tmp_path):
    """Heuristic fallback = scheduled + recent observed delay on the route."""
    snapshot = RealtimeSnapshot(
        fetched_at=SERVICE_DATE,
        stop_time_delays=[
            StopTimeDelay("T_TRAM", "R_TRAM", "STB", 2, 120)  # 2 min late
        ],
    )
    svc = PredictionService(
        repo, weather_service=_StubWeather(None), model=_no_model(tmp_path)
    )
    pred = svc.predict_travel_time(
        "R_TRAM", "STB", snapshot=snapshot, now=SERVICE_DATE
    )

    assert pred.available is True
    assert pred.scheduled_travel_min == pytest.approx(3.0)
    # expected = scheduled (3) + recent delay (2) = 5; adjustment = +2.
    assert pred.expected_travel_min == pytest.approx(5.0)
    assert pred.adjustment_min == pytest.approx(2.0)


def test_prediction_unavailable_when_stop_not_on_route(repo):
    """R5.6: transit inputs unbuildable -> available False, never raises."""
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    # STX is on R_BUS, not R_TRAM -> not part of this route.
    pred = svc.predict_travel_time("R_TRAM", "STX", now=SERVICE_DATE)

    assert pred.available is False
    assert pred.weather_used is False


def test_prediction_never_raises_when_weather_service_throws(repo):
    """A weather client that raises must not propagate (degrade to None)."""

    class _Boom:
        def current(self, lat=None, lon=None):
            raise RuntimeError("open-meteo down")

    svc = PredictionService(repo, weather_service=_Boom())
    pred = svc.predict_travel_time("R_TRAM", "STB", now=SERVICE_DATE)

    assert pred.available is True
    assert pred.weather_used is False


def test_prediction_terminus_stop_still_available(repo):
    """Selecting the last stop is a valid selection (uses default segment)."""
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    pred = svc.predict_travel_time("R_TRAM", "STD", now=SERVICE_DATE)

    assert pred.available is True
    assert pred.scheduled_travel_min == pytest.approx(DEFAULT_SEGMENT_MIN)


# --- Property 5: timeline head invariant -------------------------------------


def test_timeline_head_is_current_stop(repo):
    """R7.1/R7.2: first node is the selected stop, is_current, sequence 0."""
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    nodes = svc.arrival_timeline("R_TRAM", "STB", now=SERVICE_DATE)

    assert nodes  # STB has downstream stops
    head = nodes[0]
    assert head.stop_id == "STB"
    assert head.is_current is True
    assert head.stop_sequence == 0
    # Current stop shows the trip's real scheduled time (08:03), NOT ``now``.
    assert head.expected_arrival == _bud(8, 3)

    # Downstream nodes: STC then STD, increasing sequence, not current.
    assert [n.stop_id for n in nodes] == ["STB", "STC", "STD"]
    assert [n.stop_sequence for n in nodes] == [0, 1, 2]
    assert all(n.is_current is False for n in nodes[1:])
    # Real scheduled progression from the trip's stop_times.
    assert nodes[1].expected_arrival == _bud(8, 6)
    assert nodes[2].expected_arrival == _bud(8, 10)


def test_timeline_sequences_strictly_increasing_and_times_nondecreasing(repo):
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    nodes = svc.arrival_timeline("R_TRAM", "STA", now=SERVICE_DATE)

    seqs = [n.stop_sequence for n in nodes]
    assert seqs == sorted(seqs)
    assert seqs == list(range(len(nodes)))
    arrivals = [n.expected_arrival for n in nodes]
    assert arrivals == sorted(arrivals)


def test_timeline_terminus_returns_only_current_node(repo):
    """R7.4: no downstream stops -> only the current node."""
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    nodes = svc.arrival_timeline("R_TRAM", "STD", now=SERVICE_DATE)

    assert len(nodes) == 1
    assert nodes[0].stop_id == "STD"
    assert nodes[0].is_current is True
    assert nodes[0].stop_sequence == 0


def test_timeline_head_current_arrival_reflects_delay(repo):
    """R7.3: current-stop arrival is the trip's SCHEDULED time here + live delay.

    The timeline anchors on the next real trip (T_TRAM, scheduled 08:03 at STB),
    so a 3-minute live delay puts the current node at 08:06 — the operator's
    timetable time adjusted for the delay, not ``now`` + delay.
    """
    snapshot = RealtimeSnapshot(
        fetched_at=SERVICE_DATE,
        stop_time_delays=[StopTimeDelay("T_TRAM", "R_TRAM", "STB", 2, 180)],
    )
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    nodes = svc.arrival_timeline(
        "R_TRAM", "STB", snapshot=snapshot, now=SERVICE_DATE
    )
    # STB scheduled 08:03 + 180s delay = 08:06.
    assert nodes[0].expected_arrival == _bud(8, 6)
    # Downstream STC scheduled 08:06 + same 3-min delay = 08:09.
    assert nodes[1].expected_arrival == _bud(8, 9)


def test_timeline_unknown_stop_falls_back_to_mock(repo):
    """Unknown stop -> mock timeline (empty for an unknown mock stop)."""
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    nodes = svc.arrival_timeline("R_TRAM", "NOPE", now=SERVICE_DATE)
    assert nodes == []


# --- Trip anchoring (selected departure) -------------------------------------


def test_timeline_anchored_on_explicit_trip(repo):
    """An explicit ``trip_id`` anchors the timeline on exactly that trip.

    T_TRAM serves STB at 08:03; anchoring on it from STB yields the same real
    downstream progression (STB 08:03 -> STC 08:06 -> STD 08:10) as the
    auto-picked next trip.
    """
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    nodes = svc.arrival_timeline(
        "R_TRAM", "STB", trip_id="T_TRAM", now=SERVICE_DATE
    )

    assert [n.stop_id for n in nodes] == ["STB", "STC", "STD"]
    assert nodes[0].is_current is True
    assert nodes[0].stop_sequence == 0
    assert nodes[0].expected_arrival == _bud(8, 3)
    assert nodes[1].expected_arrival == _bud(8, 6)
    assert nodes[2].expected_arrival == _bud(8, 10)


def test_timeline_explicit_trip_applies_live_delay(repo):
    """A live delay on the anchored trip shifts every node of that trip."""
    snapshot = RealtimeSnapshot(
        fetched_at=SERVICE_DATE,
        stop_time_delays=[StopTimeDelay("T_TRAM", "R_TRAM", "STB", 2, 180)],
    )
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    nodes = svc.arrival_timeline(
        "R_TRAM", "STB", trip_id="T_TRAM", snapshot=snapshot, now=SERVICE_DATE
    )
    # 08:03 + 3 min = 08:06 at the head; downstream shifts by the same delay.
    assert nodes[0].expected_arrival == _bud(8, 6)
    assert nodes[1].expected_arrival == _bud(8, 9)


def test_timeline_unknown_trip_falls_back_to_autopick(repo):
    """An unknown/non-serving trip_id falls back to the next-trip auto-pick."""
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    anchored = svc.arrival_timeline(
        "R_TRAM", "STB", trip_id="DOES_NOT_EXIST", now=SERVICE_DATE
    )
    autopicked = svc.arrival_timeline("R_TRAM", "STB", now=SERVICE_DATE)
    assert [n.stop_id for n in anchored] == [n.stop_id for n in autopicked]
    assert [n.expected_arrival for n in anchored] == [
        n.expected_arrival for n in autopicked
    ]


# --- Mock-repo path ----------------------------------------------------------


def test_prediction_on_mock_repo(repo):
    """A mock-backed repository still yields an available prediction."""
    mock_repo = GtfsRepository.mock()
    svc = PredictionService(mock_repo, weather_service=_StubWeather(None))
    line = mock_repo.list_lines()[0]
    stop = mock_repo.stops_for_line(line.route_id)[0]

    pred = svc.predict_travel_time(line.route_id, stop.stop_id, now=SERVICE_DATE)
    assert pred.available is True
    assert pred.scheduled_travel_min == pytest.approx(DEFAULT_SEGMENT_MIN)


def test_timeline_on_mock_repo_head_invariant(repo):
    mock_repo = GtfsRepository.mock()
    svc = PredictionService(mock_repo, weather_service=_StubWeather(None))
    line = mock_repo.list_lines()[0]
    stops = mock_repo.stops_for_line(line.route_id)

    nodes = svc.arrival_timeline(line.route_id, stops[0].stop_id, now=SERVICE_DATE)
    assert nodes[0].stop_id == stops[0].stop_id
    assert nodes[0].is_current is True
    assert nodes[0].stop_sequence == 0


# --- regression: timeline uses the real timetable, not "now" -----------------


def test_timeline_current_stop_is_schedule_time_not_now(repo):
    """Bug fix: the current stop shows the trip's scheduled time, not ``now``.

    Selecting STB well before the 08:03 departure must still show 08:03 for the
    current stop (the next real train), not the moment of selection.
    """
    early_now = datetime(2024, 6, 3, 6, 0, tzinfo=timezone.utc)
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    nodes = svc.arrival_timeline("R_TRAM", "STB", now=early_now)

    assert nodes[0].stop_id == "STB"
    assert nodes[0].is_current is True
    # Real timetable time, independent of ``now``.
    assert nodes[0].expected_arrival == _bud(8, 3)
    # And it is NOT the selection time.
    assert nodes[0].expected_arrival != early_now


def test_timeline_downstream_follows_real_schedule(repo):
    """Downstream nodes use the trip's real stop_times, not synthetic segments."""
    svc = PredictionService(repo, weather_service=_StubWeather(None))
    nodes = svc.arrival_timeline("R_TRAM", "STA", now=SERVICE_DATE)
    # T_TRAM: STA 08:00, STB 08:03, STC 08:06, STD 08:10 (real schedule).
    arrivals = {n.stop_id: n.expected_arrival for n in nodes}
    assert arrivals["STA"] == _bud(8, 0)
    assert arrivals["STB"] == _bud(8, 3)
    assert arrivals["STC"] == _bud(8, 6)
    assert arrivals["STD"] == _bud(8, 10)
