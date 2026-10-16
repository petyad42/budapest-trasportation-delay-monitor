/**
 * NextRideBlock — the first stacked block of the redesigned PredictionPanel
 * (Requirement 1; data-source indicator Requirement 4).
 *
 * The block leads with the rider's next ride: given a selected `route_id` and
 * `stop_id`, it fetches upcoming departures from `/api/departures` and shows
 *   - the time-until-departure of the Upcoming_Departure (R1.1), and
 *   - that departure's live delay, colored by magnitude (R1.1, R1.3),
 * alongside a data-source badge derived from the response status (R4).
 *
 * Upcoming_Departure rule (R1.2): the first departure in backend order whose
 * realtime-adjusted `expected` time is not in the past relative to `now`. This
 * reuses `isPast` from `DeparturesPanel.tsx` so the "past" rule stays identical
 * across the app; `now` is injectable so tests are deterministic.
 *
 * When every returned departure is past (or none are returned), the block shows
 * a "no upcoming departures" state instead of a departure (R1.4).
 *
 * The query is `enabled` only when both ids are present and polls on an
 * overridable interval (mirrors `DeparturesPanel`); this component is composed
 * into `PredictionPanel` in task 8.1.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { delayColorClass } from "../lib/colors";
import { formatDelayShort, formatTimeUntil } from "../lib/predictionText";
import type { Departure } from "../lib/types";
import DataStatusBadge from "./DataStatusBadge";
import { isPast } from "./DeparturesPanel";
import { useT } from "../lib/i18n";

/** How often to re-poll departures so the next ride stays fresh (R1.5 parity). */
export const NEXT_RIDE_POLL_MS = 30_000;

/**
 * The Upcoming_Departure: the first departure in backend order whose
 * realtime-adjusted `expected` time is not in the past relative to `now`
 * (R1.2). Returns `null` when every departure is past or the list is empty.
 */
export function pickUpcomingDeparture(
  departures: Departure[],
  now: number,
): Departure | null {
  return departures.find((departure) => !isPast(departure, now)) ?? null;
}

function UpcomingDepartureDetails({
  departure,
  now,
}: {
  departure: Departure;
  now: number;
}) {
  const t = useT();
  return (
    <div className="flex flex-col gap-2" data-testid="next-ride-details">
      <div>
        <p className="text-xs uppercase tracking-wide text-slate-500">
          {t("nextride.departs")}
        </p>
        <p className="text-2xl font-semibold tabular-nums text-slate-800">
          {formatTimeUntil(departure.expected, now, t)}
        </p>
      </div>

      {/* R1.3: color the live delay by magnitude — positive red, negative
          yellow, zero slate — via the shared delay color mapping. */}
      <p
        className={`text-sm tabular-nums ${delayColorClass(departure.delay_min)}`}
        data-testid="next-ride-delay"
      >
        {formatDelayShort(departure.delay_min, t)}
      </p>
    </div>
  );
}

export interface NextRideBlockProps {
  /** Selected route id, or null when no line is chosen. */
  routeId: string | null;
  /** Selected stop id, or null when no stop is chosen. */
  stopId: string | null;
  /** "Now" used to decide which departures are past (defaults to Date.now()). */
  now?: number;
  /** Override the poll interval (primarily for tests). */
  pollIntervalMs?: number;
}

export default function NextRideBlock({
  routeId,
  stopId,
  now = Date.now(),
  pollIntervalMs = NEXT_RIDE_POLL_MS,
}: NextRideBlockProps) {
  const t = useT();
  const enabled = routeId != null && stopId != null;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["departures", routeId, stopId],
    queryFn: () => api.departures(routeId as string, stopId as string),
    enabled,
    refetchInterval: pollIntervalMs,
  });

  const departures = data?.data ?? [];
  const upcoming = pickUpcomingDeparture(departures, now);

  return (
    <section aria-label={t("nextride.title")} className="flex flex-col">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="font-medium">{t("nextride.title")}</h3>
        {/* R4: data-source indicator derived from the response status, reusing
            the DataStatusBadge vocabulary. Null status => "Data unavailable". */}
        {enabled && !isLoading && !isError ? (
          <DataStatusBadge status={data?.status ?? null} />
        ) : null}
      </div>

      {!enabled ? (
        <p className="text-sm text-slate-400">
          {t("nextride.prompt")}
        </p>
      ) : isLoading ? (
        <p className="text-sm text-slate-400">{t("nextride.loading")}</p>
      ) : isError ? (
        <p className="text-sm text-red-600">{t("nextride.error")}</p>
      ) : upcoming == null ? (
        // R1.4: every departure is past (or none exist).
        <p className="text-sm text-slate-500" data-testid="no-upcoming-departures">
          {t("nextride.empty")}
        </p>
      ) : (
        <UpcomingDepartureDetails departure={upcoming} now={now} />
      )}
    </section>
  );
}
