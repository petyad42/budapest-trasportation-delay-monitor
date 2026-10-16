"""Unit tests for :mod:`backend.delay_computer` (Requirements 1.1, 1.4, 3.1, 3.2, 3.5).

Focus areas called out by task 7 and the design test plan:
  * per-route delay math: seconds->minutes, average over all observations,
    maximum delay (R1.1, R1.4);
  * the realtime<->schedule join surfaces only modeled routes and labels them
    with the static short_name/mode;
  * problem-score ranking is by descending score (R3.1, R3.2);
  * Top-5 returns at most 5, worst first, only routes with a measurable delay,
    including the fewer-than-5 case (R3.2, R3.5).

Realtime input is built directly as :class:`RealtimeSnapshot` /
:class:`StopTimeDelay` objects (no live network); the static catalog is a tiny
in-memory GTFS zip parsed through the real loader/repository.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

import pytest

from backend.delay_computer import DelayComputer
from backend.gtfs_loader import GtfsLoader
from backend.gtfs_repository import GtfsRepository
from backend.models import Mode
from backend.realtime_service import RealtimeSnapshot, StopTimeDelay


# --- Fixture static catalog: three modeled routes + one unmodeled -----------
#
# R_TRAM (tram, type 0) short_name "4"
# R_BUS  (bus,  type 3) short_name "7"
# M2     (metro,type 1) short_name "M2"
# R_HEV  (suburban rail, type 109) -> unmodeled, must be skipped by the join.

_ROUTES = (
    "route_id,route_short_name,route_type,route_color\n"
    "R_TRAM,4,0,FFD800\n"
    "R_BUS,7,3,009EE3\n"
    "M2,M2,1,E41F18\n"
    "R_HEV,H5,109,00BBBB\n"
)
_TRIPS = (
    "trip_id,route_id,service_id,direction_id,shape_id\n"
    "T_TRAM,R_TRAM,WD,0,S1\n"
    "T_BUS,R_BUS,WD,0,S2\n"
    "T_M2,M2,WD,0,S3\n"
    "T_HEV,R_HEV,WD,0,S4\n"
)
_STOPS = (
    "stop_id,stop_name,stop_lat,stop_lon\n"
    "STA,Alpha,47.5000,19.0500\n"
    "STB,Bravo,47.5010,19.0510\n"
    "STC,Charlie,47.5020,19.0520\n"
)
_STOP_TIMES = (
    "trip_id,stop_id,arrival_time,departure_time,stop_sequence\n"
    "T_TRAM,STA,08:00:00,08:00:00,1\n"
    "T_TRAM,STB,08:03:00,08:03:00,2\n"
    "T_BUS,STA,09:00:00,09:00:00,1\n"
    "T_M2,STA,07:00:00,07:00:00,1\n"
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


def _snapshot(*delays: StopTimeDelay) -> RealtimeSnapshot:
    return RealtimeSnapshot(
        fetched_at=datetime(2024, 1, 1, 8, 0, 0, tzinfo=timezone.utc),
        feed_timestamp=1_700_000_000,
        stop_time_delays=list(delays),
    )


def _d(route_id: str, delay_seconds, *, trip="T", stop="STA", seq=1) -> StopTimeDelay:
    return StopTimeDelay(
        trip_id=trip,
        route_id=route_id,
        stop_id=stop,
        stop_sequence=seq,
        delay_seconds=delay_seconds,
    )


# --- delay math (R1.1, R1.4) ------------------------------------------------


def test_route_delays_converts_seconds_to_minutes(repo):
    # 120s and 300s -> 2.0 and 5.0 min; avg = 3.5, max = 5.0.
    snap = _snapshot(
        _d("R_BUS", 120),
        _d("R_BUS", 300),
    )
    delays = {rd.route_id: rd for rd in DelayComputer(repo, snap).route_delays()}

    bus = delays["R_BUS"]
    assert bus.avg_delay_min == pytest.approx(3.5)
    assert bus.max_delay_min == pytest.approx(5.0)
    # Labeled from the static catalog.
    assert bus.short_name == "7"
    assert bus.mode is Mode.BUS


def test_route_delays_average_includes_on_time_observations(repo):
    # Three observations: 0s (on time), 60s (1 min), 300s (5 min).
    # avg = (0 + 1 + 5) / 3 = 2.0 ; max = 5.0
    snap = _snapshot(
        _d("R_BUS", 0),
        _d("R_BUS", 60),
        _d("R_BUS", 300),
    )
    bus = {rd.route_id: rd for rd in DelayComputer(repo, snap).route_delays()}["R_BUS"]
    assert bus.avg_delay_min == pytest.approx(2.0)
    assert bus.max_delay_min == pytest.approx(5.0)


def test_route_delays_early_arrivals_count_as_zero_delay(repo):
    # Delay means *lateness*: an early arrival contributes 0, never a negative
    # delay, so neither avg nor max ever go below 0 (a route running ahead of
    # schedule is not "delayed").
    snap = _snapshot(_d("R_TRAM", -120))
    tram = {rd.route_id: rd for rd in DelayComputer(repo, snap).route_delays()}["R_TRAM"]
    assert tram.max_delay_min == pytest.approx(0.0)
    assert tram.avg_delay_min == pytest.approx(0.0)


def test_route_delays_average_blends_early_and_late(repo):
    # One early (-120s -> 0 contribution) and one late (360s -> 6 min):
    # avg = (0 + 6) / 2 = 3.0 ; max = 6.0
    snap = _snapshot(_d("R_TRAM", -120), _d("R_TRAM", 360))
    tram = {rd.route_id: rd for rd in DelayComputer(repo, snap).route_delays()}["R_TRAM"]
    assert tram.avg_delay_min == pytest.approx(3.0)
    assert tram.max_delay_min == pytest.approx(6.0)


def test_route_delays_skips_unmodeled_routes(repo):
    # R_HEV is route_type 109 (unmodeled) -> excluded from the join.
    snap = _snapshot(_d("R_HEV", 600), _d("R_BUS", 120))
    ids = {rd.route_id for rd in DelayComputer(repo, snap).route_delays()}
    assert ids == {"R_BUS"}


def test_route_delays_ignores_none_delay_observations(repo):
    snap = _snapshot(_d("R_BUS", None), _d("R_BUS", 120))
    bus = {rd.route_id: rd for rd in DelayComputer(repo, snap).route_delays()}["R_BUS"]
    # Only the 120s observation counts.
    assert bus.avg_delay_min == pytest.approx(2.0)
    assert bus.max_delay_min == pytest.approx(2.0)


def test_route_delays_empty_when_no_snapshot(repo):
    assert DelayComputer(repo, None).route_delays() == []


def test_route_delays_sorted_worst_first(repo):
    snap = _snapshot(
        _d("R_BUS", 120),   # 2 min
        _d("R_TRAM", 600),  # 10 min
        _d("M2", 300),      # 5 min
    )
    order = [rd.route_id for rd in DelayComputer(repo, snap).route_delays()]
    assert order == ["R_TRAM", "M2", "R_BUS"]


# --- problem scores + ranking (R3.1, R3.2) ----------------------------------


def test_problem_scores_rank_worse_route_higher(repo):
    # R_TRAM consistently very late; R_BUS mildly late.
    snap = _snapshot(
        _d("R_TRAM", 720), _d("R_TRAM", 900),   # 12, 15 min
        _d("R_BUS", 120), _d("R_BUS", 60),      # 2, 1 min
    )
    scores = DelayComputer(repo, snap).problem_scores()
    by_id = {ps.route_id: ps for ps in scores}

    assert by_id["R_TRAM"].score > by_id["R_BUS"].score
    # Sorted descending by score.
    assert [ps.route_id for ps in scores] == ["R_TRAM", "R_BUS"]
    # Scores are normalized into [0, 1].
    for ps in scores:
        assert 0.0 <= ps.score <= 1.0


def test_problem_score_saturates_at_one_for_extreme_delay(repo):
    # A hugely late route saturates all normalized components -> score == 1.0.
    snap = _snapshot(_d("R_TRAM", 3600), _d("R_TRAM", 3600))  # 60 min each
    tram = {ps.route_id: ps for ps in DelayComputer(repo, snap).problem_scores()}["R_TRAM"]
    assert tram.score == pytest.approx(1.0)


def test_problem_scores_empty_when_no_snapshot(repo):
    assert DelayComputer(repo, None).problem_scores() == []


# --- Top-5 ranking incl. fewer-than-5 (R3.2, R3.5) --------------------------


def test_top_problematic_returns_at_most_n_sorted(repo):
    # Six modeled-route observations across only three routes; still <= n.
    snap = _snapshot(
        _d("R_TRAM", 900),
        _d("R_BUS", 300),
        _d("M2", 120),
    )
    top = DelayComputer(repo, snap).top_problematic(n=5)
    assert [ps.route_id for ps in top] == ["R_TRAM", "R_BUS", "M2"]
    # Non-increasing scores.
    assert all(top[i].score >= top[i + 1].score for i in range(len(top) - 1))


def test_top_problematic_caps_result_size(repo):
    snap = _snapshot(
        _d("R_TRAM", 900),
        _d("R_BUS", 300),
        _d("M2", 120),
    )
    top = DelayComputer(repo, snap).top_problematic(n=2)
    assert len(top) == 2
    assert [ps.route_id for ps in top] == ["R_TRAM", "R_BUS"]


def test_top_problematic_fewer_than_5_only_delayed_routes(repo):
    # Only two routes have any positive delay; the third is perfectly on time.
    snap = _snapshot(
        _d("R_TRAM", 600),   # delayed
        _d("R_BUS", 180),    # delayed
        _d("M2", 0),         # on time -> score 0 -> excluded
    )
    top = DelayComputer(repo, snap).top_problematic(n=5)
    ids = [ps.route_id for ps in top]
    assert ids == ["R_TRAM", "R_BUS"]
    assert "M2" not in ids
    assert len(top) < 5


def test_top_problematic_excludes_on_time_and_early_routes(repo):
    # Every observation is on-time or early: nobody is "problematic".
    snap = _snapshot(
        _d("R_TRAM", 0),
        _d("R_BUS", -120),
    )
    assert DelayComputer(repo, snap).top_problematic() == []


def test_top_problematic_nonpositive_n_is_empty(repo):
    snap = _snapshot(_d("R_TRAM", 600))
    assert DelayComputer(repo, snap).top_problematic(n=0) == []
    assert DelayComputer(repo, snap).top_problematic(n=-3) == []


def test_top_problematic_empty_when_no_snapshot(repo):
    assert DelayComputer(repo, None).top_problematic() == []
