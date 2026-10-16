"""Prediction + arrival-timeline orchestration (task 12).

:class:`PredictionService` is the R5/R6/R7 orchestrator. It stitches together
pieces built in earlier tasks:

* the static schedule via :class:`~backend.gtfs_repository.GtfsRepository`
  (``stops_for_line`` for stop ordering, ``scheduled_stop_times`` for scheduled
  segment durations),
* live delays via a :class:`~backend.realtime_service.RealtimeSnapshot`,
* current weather via :class:`~backend.weather_service.WeatherService`,
* the ML regressor via :class:`~backend.travel_time_model.TravelTimeModel` and
  :class:`~backend.features.FeatureBuilder`,
* and :class:`~backend.mock_data.MockDataProvider` as the resilient fallback.

It exposes two operations from the design:

``predict_travel_time(route_id, stop_id) -> Prediction``
    Fetches weather (may be ``None``), builds features, calls the model, and
    returns ``expected`` / ``scheduled`` / ``adjustment`` plus the
    ``weather_used`` and ``available`` flags.

``arrival_timeline(route_id, stop_id) -> list[TimelineNode]``
    Returns the vertical timeline whose first node is the selected stop
    (``is_current=True``, ``stop_sequence=0``), followed by the downstream stops
    in ``stop_sequence`` order with expected arrival times.

## Weather-adjusted, not weather-dependent (Requirements 5.5, 6.4)

The single most important invariant: **missing weather never blocks a
prediction.** When Open-Meteo fails, :meth:`WeatherService.current` returns
``None``; the :class:`FeatureBuilder` then emits imputable weather columns and
the model still predicts. In that case :attr:`Prediction.weather_used` is
``False`` but :attr:`Prediction.available` stays ``True``. ``available`` is set
to ``False`` **only** when the transit inputs themselves cannot be built at all
(unknown route/stop with no schedule and no mock fallback) — Requirement 5.6.

## Model may be absent (task 10 runs in parallel)

The model artifact (``data/delay_model.joblib``) may not exist yet. The service
loads it lazily and, if it is missing or unloaded, falls back to a documented
heuristic: ``expected = scheduled_travel_min + recent_delay_min``. This keeps a
prediction available (delay-adjusted) even before the model is trained, and it
still honours the weather-independence invariant.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from backend import config
from backend.departure_service import parse_gtfs_time
from backend.features import FeatureBuilder
from backend.gtfs_repository import GtfsRepository
from backend.mock_data import MockDataProvider
from backend.models import Mode, Prediction, Stop, TimelineNode, Weather
from backend.realtime_service import RealtimeSnapshot
from backend.travel_time_model import TravelTimeModel
from backend.weather_service import WeatherService
from zoneinfo import ZoneInfo

_BUDAPEST = ZoneInfo("Europe/Budapest")

logger = logging.getLogger(__name__)

# Fallback scheduled segment duration (minutes) when the static schedule cannot
# supply one but transit inputs are otherwise available (mode-agnostic default).
DEFAULT_SEGMENT_MIN = 2.0


class PredictionService:
    """Weather/delay-adjusted travel-time predictions and arrival timelines.

    Parameters
    ----------
    repository:
        Static-schedule query layer (live or mock-backed).
    weather_service:
        Open-Meteo client. ``current`` may return ``None`` on failure; that must
        never block a prediction (R5.5, R6.4).
    model:
        The travel-time regressor. If omitted, one bound to
        :data:`config.MODEL_PATH` is created and loaded lazily. When no artifact
        exists (task 10 not yet run) the service uses a documented heuristic
        fallback instead of failing.
    feature_builder:
        Turns transit + weather inputs into the model's feature frame.
    mock:
        Deterministic fallback provider for schedules/timelines when the static
        feed has nothing usable.
    """

    def __init__(
        self,
        repository: GtfsRepository,
        weather_service: Optional[WeatherService] = None,
        model: Optional[TravelTimeModel] = None,
        feature_builder: Optional[FeatureBuilder] = None,
        mock: Optional[MockDataProvider] = None,
    ) -> None:
        self._repo = repository
        self._weather = weather_service or WeatherService()
        self._model = model if model is not None else TravelTimeModel()
        self._features = feature_builder or FeatureBuilder()
        self._mock = mock or MockDataProvider()
        # Track whether we have already attempted a lazy load so we don't retry
        # a missing artifact on every request.
        self._model_load_attempted = False

    # -- prediction -----------------------------------------------------------

    def predict_travel_time(
        self,
        route_id: str,
        stop_id: str,
        *,
        snapshot: Optional[RealtimeSnapshot] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> Prediction:
        """Return a weather/delay-adjusted :class:`Prediction` (R5.3, R5.4).

        Steps:
        1. Build the transit inputs (scheduled segment duration + mode). If they
           cannot be built at all, return ``available=False`` (R5.6).
        2. Fetch current weather; ``None`` on failure -> ``weather_used=False``
           but the prediction proceeds (R5.5, R6.4).
        3. Build features and call the model; if no model is available, use the
           documented heuristic fallback ``scheduled + recent_delay``.
        4. Report ``expected`` / ``scheduled`` / ``adjustment = expected -
           scheduled``.

        Never raises on missing weather. ``available`` is ``False`` only when the
        transit inputs are unbuildable.
        """
        reference = now or datetime.now(timezone.utc)

        transit = self._transit_inputs(route_id, stop_id, reference)
        if transit is None:
            # Requirement 5.6: transit inputs cannot be computed at all.
            return Prediction(
                expected_travel_min=0.0,
                scheduled_travel_min=0.0,
                adjustment_min=0.0,
                weather_used=False,
                available=False,
            )

        scheduled_min, mode = transit

        # Weather is optional/imputable (R5.5, R6.4): None must not block us.
        weather = self._safe_current_weather(lat, lon)
        weather_used = weather is not None

        recent_delay_min = FeatureBuilder._recent_delay_min(route_id, snapshot)

        expected_min = self._predict_expected(
            route_id=route_id,
            stop_id=stop_id,
            weather=weather,
            snapshot=snapshot,
            mode=mode,
            scheduled_min=scheduled_min,
            recent_delay_min=recent_delay_min,
            now=reference,
        )

        expected_min = round(float(expected_min), 2)
        scheduled_min = round(float(scheduled_min), 2)
        adjustment_min = round(expected_min - scheduled_min, 2)

        return Prediction(
            expected_travel_min=expected_min,
            scheduled_travel_min=scheduled_min,
            adjustment_min=adjustment_min,
            weather_used=weather_used,
            available=True,
        )

    # -- timeline -------------------------------------------------------------

    def arrival_timeline(
        self,
        route_id: str,
        stop_id: str,
        *,
        trip_id: Optional[str] = None,
        snapshot: Optional[RealtimeSnapshot] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> list[TimelineNode]:
        """Return the vertical arrival timeline for the selection (R7).

        The first node is the selected (current) stop with ``is_current=True``
        and ``stop_sequence=0``; subsequent nodes are the downstream stops in
        ``stop_sequence`` order, each with an expected arrival adjusted for
        current conditions (R7.1-R7.3). When the selected stop is the terminus,
        only the current node is returned (R7.4). An empty list means the stop is
        not on the route at all.
        """
        reference = now or datetime.now(timezone.utc)

        # Mock-backed repo has no real schedule -> deterministic mock timeline.
        if self._repo.is_mock:
            return self._mock.timeline(route_id, stop_id, now=reference)

        # Anchor the timeline on a single REAL trip. When the caller names a
        # trip (the departure the rider selected), anchor on exactly that trip so
        # every node reflects that specific run's times; otherwise auto-pick the
        # next trip serving this stop. Either way the trip's actual scheduled
        # stop_times give every node's arrival, matching the operator's timetable
        # (BKK) instead of being synthesized from "now" + estimated segments.
        anchored = None
        if trip_id is not None:
            anchored = self._trip_from_id(trip_id, stop_id, snapshot)
        if anchored is None:
            # No named trip (or it does not serve this stop) -> auto-pick.
            anchored = self._next_trip_from_stop(
                route_id, stop_id, reference, snapshot
            )
        if anchored is None:
            # No upcoming trip we can anchor to (e.g. unknown stop, end of
            # service). Fall back to mock so the UI still shows something.
            return self._mock.timeline(route_id, stop_id, now=reference)

        trip_rows, start_idx, delay_seconds = anchored
        # GTFS clock strings are Budapest local time, so anchor the service day
        # at Budapest midnight (not UTC midnight) or every stop time is skewed
        # by the UTC offset.
        day_anchor = reference.astimezone(_BUDAPEST).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        delay = timedelta(seconds=delay_seconds)

        nodes: list[TimelineNode] = []
        for offset, (sid, _seq, secs) in enumerate(trip_rows[start_idx:]):
            scheduled = day_anchor + timedelta(seconds=secs)
            expected = scheduled + delay
            stop = self._repo._stops_by_id.get(sid)
            name = stop.name if stop is not None else sid
            nodes.append(
                TimelineNode(
                    stop_id=sid,
                    name=name,
                    stop_sequence=offset,
                    expected_arrival=expected,
                    is_current=(offset == 0),
                )
            )
        return nodes

    # -- internals ------------------------------------------------------------

    def _transit_inputs(
        self, route_id: str, stop_id: str, now: datetime
    ) -> Optional[tuple[float, Optional[Mode]]]:
        """Return ``(scheduled_segment_min, mode)`` or ``None`` if unbuildable.

        The scheduled segment duration is the scheduled travel time from the
        selected stop to the next stop on the route. When there is no downstream
        stop (terminus) we still have a valid selection, so we return the
        default segment duration rather than ``None`` — a prediction is still
        meaningful for arriving *at* the current stop.

        ``None`` is returned only when the stop is not part of the route in any
        available data source (live or mock), i.e. transit inputs truly cannot
        be built (R5.6).
        """
        stops = self._downstream_stops(route_id, stop_id)
        if stops is None or not stops:
            return None

        mode = self._repo.mode_of(route_id)

        if len(stops) >= 2:
            scheduled_min = self._scheduled_segment(
                route_id, stops[0].stop_id, stops[1].stop_id, now
            )
        else:
            # Terminus: no downstream segment, use the default duration.
            scheduled_min = DEFAULT_SEGMENT_MIN
        return scheduled_min, mode

    def _downstream_stops(
        self, route_id: str, stop_id: str
    ) -> Optional[list[Stop]]:
        """Return the selected stop followed by its downstream stops, or ``None``.

        ``None`` signals "no usable static schedule for this selection" so the
        caller can decide on a mock fallback. An empty list is never returned
        here — a present-but-terminus stop yields a single-element list.
        """
        stops = self._repo.stops_for_line(route_id)
        if not stops:
            return None
        start = next(
            (i for i, s in enumerate(stops) if s.stop_id == stop_id), None
        )
        if start is None:
            return None
        return stops[start:]

    def _trip_from_id(
        self,
        trip_id: str,
        stop_id: str,
        snapshot: Optional[RealtimeSnapshot],
    ) -> Optional[tuple[list[tuple], int, int]]:
        """Resolve the anchoring tuple for an explicitly named trip.

        Returns ``(trip_rows, start_index, delay_seconds)`` for ``trip_id`` at
        ``stop_id`` in the same shape as :meth:`_next_trip_from_stop`, or
        ``None`` when the trip is unknown or does not serve ``stop_id`` (so the
        caller can fall back to auto-picking the next trip). The rider picked
        this exact departure, so we never time-filter it out: a selected trip is
        shown even if its time at ``stop_id`` is already in the past.
        """
        rows = self._repo.trip_schedule(trip_id)
        if not rows:
            return None
        start_idx = None
        for i, (sid, _seq, _secs) in enumerate(rows):
            if sid == stop_id:
                start_idx = i
                break
        if start_idx is None:
            return None
        _sid, seq, _secs = rows[start_idx]
        delay_seconds = self._lookup_trip_delay(
            self._delay_index(snapshot), trip_id, stop_id, seq
        )
        return rows, start_idx, delay_seconds

    def _next_trip_from_stop(
        self,
        route_id: str,
        stop_id: str,
        now: datetime,
        snapshot: Optional[RealtimeSnapshot],
    ) -> Optional[tuple[list[tuple], int, int]]:
        """Pick the next real trip serving ``stop_id`` and where it is in its run.

        Returns ``(trip_rows, start_index, delay_seconds)`` where ``trip_rows``
        is the anchored trip's ordered
        ``[(stop_id, stop_sequence, seconds_since_midnight)]`` (from the static
        schedule), ``start_index`` is the position of ``stop_id`` within it, and
        ``delay_seconds`` is the live delay for that trip at that stop (0 when no
        realtime). The chosen trip is the one whose delay-adjusted scheduled
        time at ``stop_id`` is the soonest at/after ``now``. Returns ``None`` when
        no trip serves the stop or none is still upcoming today.
        """
        # Budapest-local service-day midnight (GTFS clock strings are local).
        day_anchor = now.astimezone(_BUDAPEST).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        delay_index = self._delay_index(snapshot)

        best: Optional[tuple[datetime, list[tuple], int, int]] = None
        earliest: Optional[tuple[int, list[tuple], int, int]] = None
        service_date = now.astimezone(_BUDAPEST).strftime("%Y%m%d")
        for trip_id in self._repo.trips_for_route_on_date(route_id, service_date):
            rows = self._repo.trip_schedule(trip_id)
            if not rows:
                continue
            # Find this stop's position in the trip.
            start_idx = None
            for i, (sid, _seq, _secs) in enumerate(rows):
                if sid == stop_id:
                    start_idx = i
                    break
            if start_idx is None:
                continue

            _sid, seq, secs = rows[start_idx]
            delay_seconds = self._lookup_trip_delay(
                delay_index, trip_id, stop_id, seq
            )
            # Track the service-day-earliest trip as a fallback for when the
            # whole day's service is already in the past (late night): show the
            # next service's first run rather than an empty timeline.
            if earliest is None or secs < earliest[0]:
                earliest = (secs, rows, start_idx, delay_seconds)

            expected = day_anchor + timedelta(seconds=secs + delay_seconds)
            if expected < now:
                continue  # already departed this stop
            if best is None or expected < best[0]:
                best = (expected, rows, start_idx, delay_seconds)

        if best is not None:
            _expected, rows, start_idx, delay_seconds = best
            return rows, start_idx, delay_seconds
        if earliest is not None:
            _secs, rows, start_idx, delay_seconds = earliest
            return rows, start_idx, delay_seconds
        return None

    @staticmethod
    def _delay_index(snapshot: Optional[RealtimeSnapshot]) -> dict:
        """Index a snapshot's delays by ``("s",trip,stop)`` and ``("q",trip,seq)``."""
        index: dict = {}
        if snapshot is None:
            return index
        for d in snapshot.stop_time_delays:
            if d.delay_seconds is None:
                continue
            if d.stop_id:
                index[("s", d.trip_id, str(d.stop_id))] = d.delay_seconds
            if d.stop_sequence is not None:
                index[("q", d.trip_id, int(d.stop_sequence))] = d.delay_seconds
        return index

    @staticmethod
    def _lookup_trip_delay(index: dict, trip_id: str, stop_id: str, seq) -> int:
        """Delay (seconds) for a trip at a stop, matched by stop_id then seq."""
        hit = index.get(("s", trip_id, str(stop_id)))
        if hit is not None:
            return hit
        if seq is not None:
            hit = index.get(("q", trip_id, int(seq)))
            if hit is not None:
                return hit
        return 0

    def _scheduled_segment(
        self, route_id: str, from_stop_id: str, to_stop_id: str, now: datetime
    ) -> float:
        """Scheduled travel time (minutes) between two consecutive stops.

        Derived from the route's representative trip ``scheduled_stop_times``:
        the difference between the two stops' scheduled times. Falls back to
        :data:`DEFAULT_SEGMENT_MIN` when the schedule is unavailable (mock repo)
        or the times cannot be parsed.
        """
        trip_id = self._repo._rep_trip.get(route_id) if not self._repo.is_mock else None
        if trip_id is None:
            return DEFAULT_SEGMENT_MIN

        st = self._repo.scheduled_stop_times(trip_id)
        if st.empty:
            return DEFAULT_SEGMENT_MIN

        from_row = st[st["stop_id"].astype(str) == str(from_stop_id)]
        to_row = st[st["stop_id"].astype(str) == str(to_stop_id)]
        if from_row.empty or to_row.empty:
            return DEFAULT_SEGMENT_MIN

        from_time = parse_gtfs_time(
            from_row.iloc[0].get("departure_time")
            or from_row.iloc[0].get("arrival_time"),
            now,
        )
        to_time = parse_gtfs_time(
            to_row.iloc[0].get("arrival_time")
            or to_row.iloc[0].get("departure_time"),
            now,
        )
        if from_time is None or to_time is None:
            return DEFAULT_SEGMENT_MIN

        delta_min = (to_time - from_time).total_seconds() / 60.0
        # Guard against out-of-order rows / bad data.
        return delta_min if delta_min > 0 else DEFAULT_SEGMENT_MIN

    def _predict_expected(
        self,
        *,
        route_id: str,
        stop_id: str,
        weather: Optional[Weather],
        snapshot: Optional[RealtimeSnapshot],
        mode: Optional[Mode],
        scheduled_min: float,
        recent_delay_min: float,
        now: datetime,
    ) -> float:
        """Predict expected travel time (minutes) for a single segment.

        Uses the ML model when one is available; otherwise falls back to the
        documented heuristic ``scheduled + recent_delay``. Either way the result
        never depends on weather being present (R5.5, R6.4): the model imputes
        missing weather features and the heuristic ignores weather entirely.
        """
        model = self._ensure_model()
        if model is not None:
            try:
                frame = self._features.build(
                    route_id=route_id,
                    stop_id=stop_id,
                    weather=weather,
                    realtime=snapshot,
                    mode=mode,
                    scheduled_segment_min=scheduled_min,
                    now=now,
                )
                return float(model.predict(frame))
            except Exception as exc:  # noqa: BLE001 - degrade to heuristic.
                logger.warning(
                    "Model prediction failed for (%s, %s); using heuristic: %s",
                    route_id,
                    stop_id,
                    exc,
                )

        # Documented fallback: delay-adjusted scheduled time.
        return float(scheduled_min) + float(recent_delay_min)

    def _ensure_model(self) -> Optional[TravelTimeModel]:
        """Return a trained model, attempting a one-time lazy load if needed.

        Returns ``None`` when no trained model is available (artifact absent /
        task 10 not yet run), signalling the caller to use the heuristic.
        """
        if self._model.is_trained:
            return self._model
        if not self._model_load_attempted:
            self._model_load_attempted = True
            try:
                self._model.load()
            except Exception as exc:  # noqa: BLE001 - absent artifact is expected.
                logger.info(
                    "No trained travel-time model available (%s); "
                    "using heuristic fallback.",
                    exc,
                )
        return self._model if self._model.is_trained else None

    def _safe_current_weather(
        self, lat: Optional[float], lon: Optional[float]
    ) -> Optional[Weather]:
        """Fetch current weather, swallowing any error into ``None`` (R5.5)."""
        try:
            return self._weather.current(lat, lon)
        except Exception as exc:  # noqa: BLE001 - weather is optional.
            logger.warning("Weather fetch raised unexpectedly: %s", exc)
            return None


__all__ = ["PredictionService", "DEFAULT_SEGMENT_MIN"]
