"""Unit tests for :mod:`backend.colors` (Requirements 2.2, 4.2/4.3 support).

Covers the centralized mode/color mapping and BKK ``route_type`` decoding:
bus=light blue, tram=yellow, trolleybus=red, metro=official route_color.
"""
from __future__ import annotations

import pytest

from backend import colors
from backend.models import Mode


# --- route_type -> Mode ------------------------------------------------------

@pytest.mark.parametrize(
    "route_type, expected",
    [
        (0, Mode.TRAM),
        (1, Mode.METRO),
        (3, Mode.BUS),
        (11, Mode.TROLLEYBUS),
        ("0", Mode.TRAM),
        ("3", Mode.BUS),
        ("11", Mode.TROLLEYBUS),
        (3.0, Mode.BUS),
    ],
)
def test_mode_from_route_type_known_codes(route_type, expected):
    assert colors.mode_from_route_type(route_type) is expected


@pytest.mark.parametrize("route_type", [109, 4, 700, None, "", "abc", float("nan")])
def test_mode_from_route_type_unmodeled_is_none(route_type):
    assert colors.mode_from_route_type(route_type) is None


# --- mode -> color (Requirement 2.2) ----------------------------------------

def test_bus_is_light_blue():
    assert colors.color_for_mode(Mode.BUS) == colors.BUS_COLOR


def test_tram_is_yellow():
    assert colors.color_for_mode(Mode.TRAM) == colors.TRAM_COLOR


def test_trolleybus_is_red():
    assert colors.color_for_mode(Mode.TROLLEYBUS) == colors.TROLLEYBUS_COLOR


def test_non_metro_ignores_route_color():
    # Even if the feed carries a route_color, non-metro modes use their
    # canonical mode color so the coding stays consistent across the UI.
    assert colors.color_for_mode(Mode.BUS, "FF0000") == colors.BUS_COLOR
    assert colors.color_for_mode(Mode.TRAM, "123456") == colors.TRAM_COLOR


def test_metro_uses_official_route_color():
    # M2 red in the BKK feed is E41F18.
    assert colors.color_for_mode(Mode.METRO, "E41F18") == "#E41F18"
    # Accepts a leading '#' and lowercases.
    assert colors.color_for_mode(Mode.METRO, "#ffd800") == "#FFD800"


def test_metro_falls_back_when_color_missing_or_invalid():
    assert colors.color_for_mode(Mode.METRO, None) == colors.METRO_FALLBACK_COLOR
    assert colors.color_for_mode(Mode.METRO, "") == colors.METRO_FALLBACK_COLOR
    assert colors.color_for_mode(Mode.METRO, "ZZZ") == colors.METRO_FALLBACK_COLOR
    assert colors.color_for_mode(Mode.METRO, "12345") == colors.METRO_FALLBACK_COLOR


# --- route_type -> color convenience ----------------------------------------

def test_color_for_route_type():
    assert colors.color_for_route_type(3) == colors.BUS_COLOR
    assert colors.color_for_route_type(0) == colors.TRAM_COLOR
    assert colors.color_for_route_type(11) == colors.TROLLEYBUS_COLOR
    assert colors.color_for_route_type(1, "005CA5") == "#005CA5"
    # Unmodeled route type -> None.
    assert colors.color_for_route_type(109) is None
