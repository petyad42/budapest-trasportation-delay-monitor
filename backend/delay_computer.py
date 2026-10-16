"""Per-route delay metrics and problem-score ranking (task 7).

:class:`DelayComputer` joins the live GTFS-Realtime snapshot
(:class:`~backend.realtime_service.RealtimeSnapshot`) against the static
schedule exposed by :class:`~backend.gtfs_repository.GtfsRepository` and derives:

* :meth:`route_delays`    -> per-route average / maximum delay in minutes
                             (Requirements 1.1, 1.4)
* :meth:`problem_scores`  -> a single comparable "problem score" per route
                             (Requirement 3.1)
* :meth:`top_problematic` -> the Top-N most problematic routes (Requirement 3.2),
                             at most N, only routes with a measurable delay, so
                             fewer-than-N is handled naturally (Requirement 3.5)

## How realtime is joined to the schedule

Each :class:`~backend.realtime_service.StopTimeDelay` already carries the
``route_id`` (and ``trip_id``) it was reported against, so grouping the live
per-stop delays by ``route_id`` yields the current delay picture per route. The
static schedule contributes the *identity* of each route -- its human-readable
``short_name`` and transit ``mode`` -- via
:meth:`GtfsRepository.list_lines`, so the metrics carry the same labels the UI
uses everywhere else. Routes that appear in the realtime feed but are not part
of the modeled static catalog (e.g. an unmodeled route_type, or a stale id) are
skipped rather than surfaced without a mode/color.

## Problem score (Requirement 3.1)

The design specifies a *weighted, normalized* blend of three signals so routes
are comparable across modes:

1. **mean delay** on the route -- sustained, everyday pain;
2. **share of observations delayed** beyond a threshold -- how often the route
   is meaningfully late (0..1, already normalized);
3. **max delay** on the route -- worst-case spikes.

The two minute-valued signals are normalized to a 0..1 scale by dividing by a
configurable reference delay (:data:`DEFAULT_DELAY_SCALE_MIN`) and clamping, so
a route that is, say, 15 min late does not dwarf every other signal and so the
blend stays mode-agnostic. The three normalized components are combined with
configurable weights (:data:`DEFAULT_WEIGHTS`) that sum to 1, giving a final
score in ``[0, 1]``. Weights and thresholds are module-level constants,
documented here and overridable per instance.

Only *positive* delays contribute to "problem"; early / on-time observations
(delay <= 0) pull the average toward zero but never produce a negative score.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from backend.gtfs_repository import GtfsRepository
from backend.models import Mode, ProblemScore, RouteDelay
from backend.realtime_service import RealtimeSnapshot

# Seconds per minute; realtime delays arrive in seconds, the UI wants minutes.
_SECONDS_PER_MIN = 60.0

# A delay (minutes) at/above this reference maps to the top of the 0..1
# normalization band. Chosen so a quarter-hour-late route saturates the
# minute-valued components; tune in one place if the feed's delay profile
# differs.
DEFAULT_DELAY_SCALE_MIN = 15.0

# An observation is counted as "meaningfully delayed" when its delay meets or
# exceeds this many minutes. Feeds report tiny sub-minute jitter constantly;
# this threshold keeps the "share delayed" signal about real lateness.
DEFAULT_DELAY_THRESHOLD_MIN = 2.0

# Blend weights for the three normalized problem-score components. They sum to
# 1 so the resulting score stays in [0, 1]. Weighted toward the mean (sustained
# delay) with a meaningful share-delayed term and a smaller worst-case term.
DEFAULT_WEIGHTS = {
    "avg": 0.5,
    "share": 0.3,
    "max": 0.2,
}


@dataclass
class _RouteStats:
    """Intermediate per-route accumulation of positive-delay observations."""

    route_id: str
    delays_min: List[float]  # only delays > 0 are collected here
    observation_count: int  # total observations seen (incl. on-time/early)

    @property
    def avg_delay_min(self) -> float:
        """Mean delay in minutes over *all* observations (on-time count too)."""
        if self.observation_count == 0:
            return 0.0
        return sum(self.delays_min) / self.observation_count

    @property
    def max_delay_min(self) -> float:
        """Largest observed delay in minutes; 0 when nothing was late."""
        return max(self.delays_min, default=0.0)

    def share_delayed(self, threshold_min: float) -> float:
        """Fraction of observations at/above ``threshold_min`` (0..1)."""
        if self.observation_count == 0:
            return 0.0
        late = sum(1 for d in self.delays_min if d >= threshold_min)
        return late / self.observation_count


def _clamp01(value: float) -> float:
    """Clamp ``value`` into the closed ``[0, 1]`` interval."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


class DelayComputer:
    """Computes per-route delays and problem scores from a realtime snapshot.

    Parameters
    ----------
    repository:
        The static GTFS query layer; supplies each route's ``short_name`` and
        ``mode`` and defines which routes are modeled.
    snapshot:
        The latest :class:`RealtimeSnapshot`. ``None`` (never polled) is treated
        as "no realtime observations", yielding empty results rather than an
        error.
    delay_scale_min, delay_threshold_min, weights:
        Problem-score tuning knobs (see module docstring). Defaults come from the
        module-level constants.
    """

    def __init__(
        self,
        repository: GtfsRepository,
        snapshot: Optional[RealtimeSnapshot],
        *,
        delay_scale_min: float = DEFAULT_DELAY_SCALE_MIN,
        delay_threshold_min: float = DEFAULT_DELAY_THRESHOLD_MIN,
        weights: Optional[Dict[str, float]] = None,
    ) -> None:
        self._repo = repository
        self._snapshot = snapshot
        self._delay_scale_min = delay_scale_min
        self._delay_threshold_min = delay_threshold_min
        self._weights = dict(weights) if weights is not None else dict(DEFAULT_WEIGHTS)

        # Route identity from the static catalog: route_id -> (short_name, mode).
        self._lines_by_id = {
            line.route_id: line for line in self._repo.list_lines()
        }

    # -- realtime/schedule join ----------------------------------------------

    def _route_stats(self) -> Dict[str, _RouteStats]:
        """Group the snapshot's per-stop delays by route (the RT<->schedule join).

        Only routes present in the static catalog are kept, so every emitted
        metric can be labeled with a ``short_name`` and ``mode``. Observations
        with a missing (``None``) delay are ignored; on-time/early observations
        count toward the total but contribute no positive delay.
        """
        stats: Dict[str, _RouteStats] = {}
        if self._snapshot is None:
            return stats

        for obs in self._snapshot.stop_time_delays:
            route_id = obs.route_id
            if route_id not in self._lines_by_id:
                continue  # unmodeled / unknown route -> not surfaced
            if obs.delay_seconds is None:
                continue  # no delay reported for this stop

            entry = stats.get(route_id)
            if entry is None:
                entry = _RouteStats(route_id=route_id, delays_min=[], observation_count=0)
                stats[route_id] = entry

            entry.observation_count += 1
            delay_min = obs.delay_seconds / _SECONDS_PER_MIN
            if delay_min > 0.0:
                entry.delays_min.append(delay_min)

        return stats

    # -- public API -----------------------------------------------------------

    def route_delays(self) -> List[RouteDelay]:
        """Per-route average and maximum delay in minutes (R1.1, R1.4).

        Sorted by descending average delay (worst first) then ``route_id`` for a
        stable order. Routes with no realtime observations are omitted.
        """
        out: List[RouteDelay] = []
        for route_id, stats in self._route_stats().items():
            line = self._lines_by_id[route_id]
            out.append(
                RouteDelay(
                    route_id=route_id,
                    short_name=line.short_name,
                    mode=line.mode,
                    avg_delay_min=round(stats.avg_delay_min, 1),
                    max_delay_min=round(stats.max_delay_min, 1),
                )
            )
        out.sort(key=lambda rd: (-rd.avg_delay_min, rd.route_id))
        return out

    def _score_for(self, stats: _RouteStats) -> float:
        """Compute the normalized 0..1 problem score for one route.

        Blends normalized mean delay, share-delayed, and normalized max delay
        with the configured weights (see module docstring).
        """
        scale = self._delay_scale_min if self._delay_scale_min > 0 else 1.0
        avg_norm = _clamp01(stats.avg_delay_min / scale)
        max_norm = _clamp01(stats.max_delay_min / scale)
        share = _clamp01(stats.share_delayed(self._delay_threshold_min))

        score = (
            self._weights.get("avg", 0.0) * avg_norm
            + self._weights.get("share", 0.0) * share
            + self._weights.get("max", 0.0) * max_norm
        )
        return _clamp01(score)

    def problem_scores(self) -> List[ProblemScore]:
        """All observed routes scored and sorted by descending score (R3.1).

        Ties break on ``route_id`` for deterministic ordering.
        """
        out: List[ProblemScore] = []
        for route_id, stats in self._route_stats().items():
            line = self._lines_by_id[route_id]
            out.append(
                ProblemScore(
                    route_id=route_id,
                    short_name=line.short_name,
                    mode=line.mode,
                    score=round(self._score_for(stats), 4),
                    avg_delay_min=round(stats.avg_delay_min, 1),
                )
            )
        out.sort(key=lambda ps: (-ps.score, ps.route_id))
        return out

    def top_problematic(self, n: int = 5) -> List[ProblemScore]:
        """Top-``n`` routes by problem score, worst first (R3.2, R3.5).

        Only routes with a *measurable* delay (score > 0, i.e. some positive
        delay was observed) are eligible, so if fewer than ``n`` routes are
        actually delayed, only those are returned (R3.5). A non-positive ``n``
        yields an empty list.
        """
        if n <= 0:
            return []
        eligible = [ps for ps in self.problem_scores() if ps.score > 0.0]
        return eligible[:n]


__all__ = [
    "DelayComputer",
    "DEFAULT_DELAY_SCALE_MIN",
    "DEFAULT_DELAY_THRESHOLD_MIN",
    "DEFAULT_WEIGHTS",
]
