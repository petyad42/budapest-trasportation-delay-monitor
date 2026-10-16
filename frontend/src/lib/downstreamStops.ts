/**
 * Pure downstream-stop filter for the destination selector (Requirement 2.1).
 *
 * Kept React-free in `lib/` so it can be unit/property tested in isolation
 * (design Property 6: "Downstream filter excludes origin and earlier stops").
 */
import type { Stop } from "./types";

/**
 * Stops strictly downstream of the origin on the active direction.
 *
 * Mirrors the design contract exactly: find the origin's index in the ordered
 * active-direction stop list and take everything after it —
 *   `stops.slice(stops.findIndex(s => s.stop_id === originStopId) + 1)`
 * — so the origin and every stop before it are excluded.
 *
 * When the origin is absent (`findIndex` returns -1) a naive `slice(0)` would
 * return the whole list and wrongly offer upstream stops, so a missing or
 * mismatched origin yields an empty list instead.
 */
export function downstreamStops(
  stops: Stop[],
  originStopId: string | null,
): Stop[] {
  if (originStopId == null) return [];
  const originIndex = stops.findIndex((s) => s.stop_id === originStopId);
  if (originIndex < 0) return [];
  return stops.slice(originIndex + 1);
}
