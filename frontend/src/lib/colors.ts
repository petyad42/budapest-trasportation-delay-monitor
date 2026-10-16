/**
 * Shared transit mode/color mapping for the frontend.
 *
 * This is the single source of truth for mode coloring on the map, in the
 * lines/legend lists, and anywhere else the UI shows a mode. It mirrors the
 * backend module `backend/colors.py` so the two never drift apart
 * (design "Property 8: Mode-color consistency", Requirement 2.2):
 *
 *   - bus         -> light blue
 *   - tram        -> yellow
 *   - trolleybus  -> red
 *   - metro       -> the line's official GTFS route_color (fallback deep blue)
 *
 * The hex values are kept identical to the backend constants.
 */

export type Mode = "bus" | "tram" | "trolleybus" | "metro";

// Canonical per-mode colors. Must match backend/colors.py.
export const BUS_COLOR = "#4FC3F7"; // light blue
export const TRAM_COLOR = "#FFD800"; // yellow
export const TROLLEYBUS_COLOR = "#E53935"; // red
export const METRO_FALLBACK_COLOR = "#004A99"; // deep blue, only if route_color missing

const MODE_COLOR: Record<Mode, string> = {
  bus: BUS_COLOR,
  tram: TRAM_COLOR,
  trolleybus: TROLLEYBUS_COLOR,
  metro: METRO_FALLBACK_COLOR,
};

/**
 * Normalize a GTFS route_color to `#RRGGBB`, or return null if invalid.
 *
 * GTFS stores colors as 6 hex digits with no leading `#` (e.g. `FFD800`).
 * Blank or malformed values yield null.
 */
export function normalizeHex(color: string | null | undefined): string | null {
  if (color == null) return null;
  const text = String(color).trim().replace(/^#/, "");
  if (text.length !== 6) return null;
  if (!/^[0-9a-fA-F]{6}$/.test(text)) return null;
  return `#${text.toUpperCase()}`;
}

/**
 * Return the display color for a mode (Requirement 2.2).
 *
 * For metro, the line's official `routeColor` is used when a valid one is
 * supplied; otherwise the metro fallback. Bus, tram, and trolleybus always use
 * their canonical mode color regardless of any route_color in the feed.
 */
export function colorForMode(
  mode: Mode,
  routeColor?: string | null,
): string {
  if (mode === "metro") {
    return normalizeHex(routeColor) ?? METRO_FALLBACK_COLOR;
  }
  return MODE_COLOR[mode];
}

/**
 * Semantic Tailwind text-color class for a delay value, shared by the
 * departures list and the arrival timeline so delay/rush coloring stays
 * consistent across the UI.
 *
 *   - late  (delayMin > 0) -> red
 *   - rush  (delayMin < 0) -> yellow (ahead of schedule / early)
 *   - on time (delayMin === 0) -> default muted slate
 */
export function delayColorClass(delayMin: number): string {
  if (delayMin > 0) return "text-red-600";
  if (delayMin < 0) return "text-yellow-500";
  return "text-slate-500";
}
