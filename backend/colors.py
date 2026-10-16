"""Centralized transit mode/color mapping (single source of truth).

Requirement 2.2 defines the color coding for the map, lists, and legend:

* bus         -> light blue
* tram        -> yellow
* trolleybus  -> red
* metro       -> the metro line's own **official** color (from GTFS ``route_color``)

Keeping this mapping in one place prevents drift between the map, the local
lines list, and the legend (design "Property 8: Mode-color consistency"). The
frontend mirrors these same values in its own shared color module (task 14).

## BKK GTFS ``route_type`` codes

Confirmed against the live BKK GTFS static feed (``routes.txt``):

| route_type | mode        | count in feed |
|------------|-------------|---------------|
| 3          | bus         | 306           |
| 0          | tram        | 45            |
| 11         | trolleybus  | 16            |
| 1          | metro       | 4             |
| 109        | suburban rail (HÉV) | 5     |
| 4          | ferry       | 1             |

Only the four modes the application cares about (bus, tram, trolleybus, metro)
are modeled by :class:`~backend.models.Mode`. Route types outside that set
(e.g. 109 HÉV, 4 ferry) are reported by :func:`mode_from_route_type` as
``None`` so callers can skip them rather than mis-color them.
"""
from __future__ import annotations

from typing import Optional

from backend.models import Mode

# --- Canonical per-mode colors (Requirement 2.2) ----------------------------
# Metro is special-cased: it uses the line's official route_color when present
# and only falls back to METRO_FALLBACK when a feed omits the color.
BUS_COLOR = "#4FC3F7"          # light blue
TRAM_COLOR = "#FFD800"         # yellow
TROLLEYBUS_COLOR = "#E53935"   # red
METRO_FALLBACK_COLOR = "#004A99"  # deep blue, only if route_color missing

_MODE_COLOR: dict[Mode, str] = {
    Mode.BUS: BUS_COLOR,
    Mode.TRAM: TRAM_COLOR,
    Mode.TROLLEYBUS: TROLLEYBUS_COLOR,
    Mode.METRO: METRO_FALLBACK_COLOR,
}

# --- GTFS route_type -> Mode ------------------------------------------------
# Confirmed against the BKK feed (see module docstring). Values not present
# here (109 HÉV, 4 ferry, ...) intentionally map to None.
_ROUTE_TYPE_MODE: dict[int, Mode] = {
    0: Mode.TRAM,
    1: Mode.METRO,
    3: Mode.BUS,
    11: Mode.TROLLEYBUS,
}


def mode_from_route_type(route_type: object) -> Optional[Mode]:
    """Map a GTFS ``route_type`` to a :class:`Mode`, or ``None`` if unmodeled.

    Accepts ints, numeric strings, or floats (pandas may hand back any of
    these). Returns ``None`` for route types the application does not model
    (e.g. suburban rail / ferry) or for unparseable values.
    """
    if route_type is None:
        return None
    try:
        code = int(float(route_type))
    except (TypeError, ValueError):
        return None
    return _ROUTE_TYPE_MODE.get(code)


def _normalize_hex(color: object) -> Optional[str]:
    """Return a ``#RRGGBB`` string for a GTFS ``route_color`` or ``None``.

    GTFS stores colors as 6 hex digits without the leading ``#`` (e.g.
    ``FFD800``). Blank/invalid values yield ``None``.
    """
    if color is None:
        return None
    text = str(color).strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        int(text, 16)
    except ValueError:
        return None
    return f"#{text.upper()}"


def color_for_mode(mode: Mode, route_color: object = None) -> str:
    """Return the display color for ``mode`` (Requirement 2.2).

    For metro, the line's official ``route_color`` (from GTFS) is used when a
    valid one is supplied; otherwise :data:`METRO_FALLBACK_COLOR`. Bus, tram,
    and trolleybus always use their canonical mode color regardless of any
    ``route_color`` in the feed, keeping the coding consistent across the UI.
    """
    if mode is Mode.METRO:
        return _normalize_hex(route_color) or METRO_FALLBACK_COLOR
    return _MODE_COLOR[mode]


def color_for_route_type(route_type: object, route_color: object = None) -> Optional[str]:
    """Convenience: map a GTFS ``route_type`` straight to a color.

    Returns ``None`` when the route type is not a modeled mode.
    """
    mode = mode_from_route_type(route_type)
    if mode is None:
        return None
    return color_for_mode(mode, route_color)


__all__ = [
    "BUS_COLOR",
    "TRAM_COLOR",
    "TROLLEYBUS_COLOR",
    "METRO_FALLBACK_COLOR",
    "mode_from_route_type",
    "color_for_mode",
    "color_for_route_type",
]
