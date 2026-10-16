"""Unit tests for the deterministic MockDataProvider (Task 5).

Covers the catalog (lines/stops), delays, problem-score ranking, departures,
and the arrival timeline, plus the core guarantee that output is deterministic
across independently-constructed providers (Requirements 1.3, 9.3, 10.3).
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.mock_data import DEFAULT_NOW, MockDataProvider
from backend.models import Departure, Line, ProblemScore, RouteDelay, Stop, TimelineNode


# -- catalog ------------------------------------------------------------------


def test_lines_are_nonempty_and_typed():
    provider = MockDataProvider()
    lines = provider.lines()
    assert lines
    assert all(isinstance(line, Line) for line in lines)
    # route_ids are unique
    ids = [line.route_id for line in lines]
    assert len(ids) == len(set(ids))


def test_stops_for_line_scoped_and_nonempty():
    provider = MockDataProvider()
    for line in provider.lines():
        stops = provider.stops_for_line(line.route_id)
        assert stops, f"no stops for {line.route_id}"
        assert all(isinstance(stop, Stop) for stop in stops)
        # every stop id is namespaced to its route (scoping)
        assert all(stop.stop_id.startswith(f"MOCK-{line.route_id}-") for stop in stops)


def test_stops_for_unknown_line_is_empty():
    provider = MockDataProvider()
    assert provider.stops_for_line("does-not-exist") == []


def test_metro_line_uses_official_color():
    provider = MockDataProvider()
    m2 = next(line for line in provider.lines() if line.route_id == "M2")
    assert m2.color == "#E41F18"


# -- delays & ranking ---------------------------------------------------------


def test_route_delays_cover_all_lines():
    provider = MockDataProvider()
    delays = provider.route_delays()
    assert {d.route_id for d in delays} == {line.route_id for line in provider.lines()}
    assert all(isinstance(d, RouteDelay) for d in delays)


def test_max_delay_never_below_avg():
    provider = MockDataProvider()
    for d in provider.route_delays():
        assert d.max_delay_min >= d.avg_delay_min


def test_top_problematic_size_and_ordering():
    provider = MockDataProvider()
    top = provider.top_problematic(5)
    assert isinstance(top, list)
    assert len(top) <= 5
    assert all(isinstance(s, ProblemScore) for s in top)
    scores = [s.score for s in top]
    assert scores == sorted(scores, reverse=True)


def test_top_problematic_respects_smaller_n():
    provider = MockDataProvider()
    assert len(provider.top_problematic(3)) <= 3


# -- departures ---------------------------------------------------------------


def test_departures_scoped_to_valid_stop():
    provider = MockDataProvider()
    line = provider.lines()[0]
    stop = provider.stops_for_line(line.route_id)[0]
    deps = provider.departures(line.route_id, stop.stop_id, limit=5)
    assert len(deps) == 5
    assert all(isinstance(d, Departure) for d in deps)


def test_departures_unknown_stop_is_empty():
    provider = MockDataProvider()
    line = provider.lines()[0]
    assert provider.departures(line.route_id, "MOCK-bogus-S99") == []


def test_departure_expected_equals_scheduled_plus_delay():
    provider = MockDataProvider()
    line = provider.lines()[0]
    stop = provider.stops_for_line(line.route_id)[0]
    for d in provider.departures(line.route_id, stop.stop_id, limit=8):
        delta_min = (d.expected - d.scheduled).total_seconds() / 60.0
        assert round(delta_min, 1) == d.delay_min


def test_departures_are_chronologically_ordered():
    provider = MockDataProvider()
    line = provider.lines()[0]
    stop = provider.stops_for_line(line.route_id)[0]
    deps = provider.departures(line.route_id, stop.stop_id, limit=6)
    scheduled = [d.scheduled for d in deps]
    assert scheduled == sorted(scheduled)


# -- timeline -----------------------------------------------------------------


def test_timeline_head_is_current_stop():
    provider = MockDataProvider()
    line = provider.lines()[0]
    stops = provider.stops_for_line(line.route_id)
    nodes = provider.timeline(line.route_id, stops[0].stop_id)
    assert nodes
    assert nodes[0].is_current is True
    assert nodes[0].stop_id == stops[0].stop_id
    assert [n.stop_sequence for n in nodes] == list(range(len(nodes)))
    assert all(isinstance(n, TimelineNode) for n in nodes)


def test_timeline_terminus_has_single_node():
    provider = MockDataProvider()
    line = provider.lines()[0]
    stops = provider.stops_for_line(line.route_id)
    nodes = provider.timeline(line.route_id, stops[-1].stop_id)
    assert len(nodes) == 1
    assert nodes[0].is_current is True


def test_timeline_arrivals_are_non_decreasing():
    provider = MockDataProvider()
    line = provider.lines()[0]
    stops = provider.stops_for_line(line.route_id)
    nodes = provider.timeline(line.route_id, stops[0].stop_id)
    arrivals = [n.expected_arrival for n in nodes]
    assert arrivals == sorted(arrivals)


# -- determinism (the core guarantee) -----------------------------------------


def test_catalog_is_deterministic_across_instances():
    a = MockDataProvider()
    b = MockDataProvider()
    assert [l.model_dump() for l in a.lines()] == [l.model_dump() for l in b.lines()]
    route_id = a.lines()[0].route_id
    assert (
        [s.model_dump() for s in a.stops_for_line(route_id)]
        == [s.model_dump() for s in b.stops_for_line(route_id)]
    )


def test_delays_and_scores_are_deterministic():
    a = MockDataProvider()
    b = MockDataProvider()
    assert [d.model_dump() for d in a.route_delays()] == [
        d.model_dump() for d in b.route_delays()
    ]
    assert [s.model_dump() for s in a.top_problematic(5)] == [
        s.model_dump() for s in b.top_problematic(5)
    ]


def test_departures_deterministic_for_same_now():
    now = datetime(2024, 5, 1, 9, 30, tzinfo=timezone.utc)
    a = MockDataProvider()
    b = MockDataProvider()
    line = a.lines()[0]
    stop = a.stops_for_line(line.route_id)[0]
    da = a.departures(line.route_id, stop.stop_id, limit=5, now=now)
    db = b.departures(line.route_id, stop.stop_id, limit=5, now=now)
    assert [d.model_dump() for d in da] == [d.model_dump() for d in db]


def test_different_seed_changes_output():
    a = MockDataProvider(seed=1)
    b = MockDataProvider(seed=2)
    # Same catalog identity (lines/stops are fixed), but metrics differ.
    assert a.route_delays() != b.route_delays()


def test_default_now_anchor_is_used():
    provider = MockDataProvider()
    line = provider.lines()[0]
    stop = provider.stops_for_line(line.route_id)[0]
    deps = provider.departures(line.route_id, stop.stop_id, limit=1)
    # First scheduled departure is at or after the fixed anchor.
    assert deps[0].scheduled >= DEFAULT_NOW
