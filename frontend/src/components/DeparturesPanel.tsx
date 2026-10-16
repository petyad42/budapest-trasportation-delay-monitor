/**
 * DeparturesPanel — the departures column of the "Departures + prediction" view
 * (Requirement 5, specifically R5.1 and R5.2).
 *
 * Given a selected `route_id` and `stop_id`, fetches upcoming departures from
 * `/api/departures?route_id&stop_id` and lists each one with:
 *   - its scheduled time (R5.2),
 *   - its current delay in a human-readable format (R5.2, R1.4).
 *
 * When no line/stop is selected the panel shows a prompt state instead of
 * making a request (the query stays disabled until both ids are present).
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { delayColorClass } from "../lib/colors";
import type { Departure } from "../lib/types";
import { useT, type Translate } from "../lib/i18n";

/** How often to re-poll departures so delays stay fresh (R1.5). */
export const DEPARTURES_POLL_MS = 30_000;

/** Format an ISO-8601 timestamp as a local `HH:MM` clock time. */
export function formatClock(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/** Human-readable delay, e.g. `+3 min`, `-1 min`, or `on time` (R5.2). */
export function formatDelay(delayMin: number, t?: Translate): string {
  const rounded = Math.round(delayMin * 10) / 10;
  if (rounded === 0) return t ? t("departures.onTime") : "on time";
  const sign = rounded > 0 ? "+" : "";
  return `${sign}${rounded} min`;
}

/**
 * Whether a departure is in the past relative to `now`, using the
 * realtime-adjusted `expected` time (matches the app's realtime emphasis).
 * An unparseable timestamp is treated as not past so it stays interactive.
 */
export function isPast(departure: Departure, now: number): boolean {
  const expected = new Date(departure.expected).getTime();
  if (Number.isNaN(expected)) return false;
  return expected < now;
}

function DepartureRow({
  departure,
  isSelected,
  isPast: past,
  onSelect,
  t,
}: {
  departure: Departure;
  isSelected: boolean;
  isPast: boolean;
  onSelect: (departure: Departure | null) => void;
  t: Translate;
}) {
  // Past departures read as inactive: mute both the clock and the delay text.
  const clockClass = past ? "text-slate-400" : "text-slate-800";
  const delayClass = past ? "text-slate-400" : delayColorClass(departure.delay_min);

  return (
    <li role="option" aria-selected={isSelected}>
      <button
        type="button"
        onClick={() => onSelect(isSelected ? null : departure)}
        className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-slate-50 ${
          isSelected ? "bg-slate-100 font-semibold" : ""
        }`}
      >
        <span className={`font-medium tabular-nums ${clockClass}`}>
          {formatClock(departure.scheduled)}
        </span>
        <span className={`shrink-0 text-sm tabular-nums ${delayClass}`}>
          {formatDelay(departure.delay_min, t)}
        </span>
      </button>
    </li>
  );
}

export interface DeparturesPanelProps {
  /** Selected route id, or null when no line is chosen. */
  routeId: string | null;
  /** Selected stop id, or null when no stop is chosen. */
  stopId: string | null;
  /** The currently selected departure (lifted state), or null when none. */
  selectedDeparture: Departure | null;
  /**
   * Called when the user clicks a departure. Passes the clicked departure, or
   * `null` when the user clicks the already-selected row to clear it.
   */
  onSelectDeparture: (departure: Departure | null) => void;
  /** "Now" used to decide which departures are past (defaults to Date.now()). */
  now?: number;
  /** Override the poll interval (primarily for tests). */
  pollIntervalMs?: number;
}

export default function DeparturesPanel({
  routeId,
  stopId,
  selectedDeparture,
  onSelectDeparture,
  now = Date.now(),
  pollIntervalMs = DEPARTURES_POLL_MS,
}: DeparturesPanelProps) {
  const t = useT();
  const enabled = routeId != null && stopId != null;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["departures", routeId, stopId],
    queryFn: () => api.departures(routeId as string, stopId as string),
    enabled,
    refetchInterval: pollIntervalMs,
  });

  const departures = data?.data ?? [];

  return (
    <section aria-label={t("departures.aria")} className="flex flex-col">
      <h3 className="mb-2 font-medium">{t("departures.title")}</h3>

      {!enabled ? (
        <p className="text-sm text-slate-400">
          {t("departures.prompt")}
        </p>
      ) : isLoading ? (
        <p className="text-sm text-slate-400">{t("departures.loading")}</p>
      ) : isError ? (
        <p className="text-sm text-red-600">{t("departures.error")}</p>
      ) : departures.length === 0 ? (
        <p className="text-sm text-slate-400">{t("departures.empty")}</p>
      ) : (
        <ol
          role="listbox"
          aria-label={t("departures.aria")}
          className="max-h-56 divide-y divide-slate-100 overflow-y-auto rounded-md border border-slate-200"
        >
          {departures.map((departure) => (
            <DepartureRow
              key={departure.trip_id}
              departure={departure}
              isSelected={
                selectedDeparture?.trip_id === departure.trip_id
              }
              isPast={isPast(departure, now)}
              onSelect={onSelectDeparture}
              t={t}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
