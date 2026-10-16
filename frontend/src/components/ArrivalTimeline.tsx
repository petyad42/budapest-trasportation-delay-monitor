/**
 * ArrivalTimeline — the vertical arrival timeline (Requirement 7).
 *
 * Given a selected route and stop, fetches the timeline nodes from
 * `/api/timeline?route_id&stop_id` and renders them as a vertical list where:
 *   - the first node is the selected (current) stop, marked `is_current` (R7.1),
 *   - subsequent stops follow in `stop_sequence` order (R7.2),
 *   - each node shows its expected arrival time, adjusted for current
 *     conditions (R7.3).
 *
 * The backend guarantees the ordering (current stop first, then downstream
 * stops by increasing `stop_sequence` — see design.md "Timeline head
 * invariant"), so the component renders the nodes as delivered. When there are
 * no downstream stops the list contains only the current node (R7.4), and when
 * neither a route nor a stop is selected the component shows a prompt instead
 * of fetching.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { delayColorClass } from "../lib/colors";
import { useT } from "../lib/i18n";
import type { Departure, TimelineNode } from "../lib/types";

export interface ArrivalTimelineProps {
  /** Selected route id, or null when no line is chosen. */
  routeId: string | null;
  /** Selected stop id, or null when no stop is chosen. */
  stopId: string | null;
  /**
   * The departure the user picked in the departures list, or null/undefined to
   * keep the default next-trip behavior. When present it drives the current
   * (head) node's arrival time and delay/rush color.
   */
  selectedDeparture?: Departure | null;
}

/**
 * Format an ISO-8601 arrival timestamp as a local wall-clock time (HH:MM).
 * Falls back to the raw string if it can't be parsed so we never render
 * "Invalid Date" to the user.
 */
export function formatArrival(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function TimelineRow({
  node,
  isLast,
  selectedDeparture,
}: {
  node: TimelineNode;
  isLast: boolean;
  selectedDeparture?: Departure | null;
}) {
  // The timeline is anchored on the selected trip, so every node already
  // carries that trip's expected arrival. We render each node's own time and
  // only tint the current (head) node by the selected departure's delay/rush so
  // the head stays visually consistent with the departures list.
  const usesSelection = node.is_current && selectedDeparture != null;
  const arrivalIso = node.expected_arrival;
  const timeColorClass = usesSelection
    ? delayColorClass(selectedDeparture.delay_min)
    : "text-slate-600";
  const t = useT();

  return (
    <li className="flex gap-3">
      {/* Node marker + connecting line */}
      <div className="flex flex-col items-center">
        <span
          aria-hidden="true"
          className={`mt-1 inline-block h-3 w-3 shrink-0 rounded-full ${
            node.is_current
              ? "bg-slate-800 ring-2 ring-slate-300"
              : "border-2 border-slate-400 bg-white"
          }`}
        />
        {!isLast && (
          <span
            aria-hidden="true"
            className="w-px flex-1 bg-slate-200"
            style={{ minHeight: "1rem" }}
          />
        )}
      </div>

      {/* Stop name + expected arrival */}
      <div className="min-w-0 flex-1 pb-4">
        <div className="flex items-baseline justify-between gap-2">
          <span
            className={`truncate ${
              node.is_current ? "font-semibold text-slate-900" : "text-slate-700"
            }`}
          >
            {node.name}
          </span>
          <span className={`shrink-0 text-sm tabular-nums ${timeColorClass}`}>
            {formatArrival(arrivalIso)}
          </span>
        </div>
        {node.is_current && (
          <span className="text-xs uppercase tracking-wide text-slate-400">
            {t("timeline.currentStop")}
          </span>
        )}
      </div>
    </li>
  );
}

export default function ArrivalTimeline({
  routeId,
  stopId,
  selectedDeparture,
}: ArrivalTimelineProps) {
  const t = useT();
  const enabled = routeId != null && stopId != null;

  // Anchor on the rider's selected departure when present: the timeline then
  // reflects that exact trip's per-stop times, not just the head node. The
  // trip_id is part of the key so changing the selection refetches.
  const tripId = selectedDeparture?.trip_id ?? null;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["timeline", routeId, stopId, tripId],
    queryFn: async () =>
      (
        await api.timeline(
          routeId as string,
          stopId as string,
          tripId ?? undefined,
        )
      ).data,
    enabled,
  });

  const nodes = data ?? [];

  return (
    <section aria-label={t("timeline.title")}>
      <h3 className="mb-2 font-medium">{t("timeline.title")}</h3>

      {!enabled ? (
        <p className="text-sm text-slate-400">
          {t("timeline.prompt")}
        </p>
      ) : isLoading ? (
        <p className="text-sm text-slate-400">{t("timeline.loading")}</p>
      ) : isError ? (
        <p className="text-sm text-red-600">
          {t("timeline.error")}
        </p>
      ) : nodes.length === 0 ? (
        // No timeline nodes at all (e.g. no upcoming service).
        <p className="text-sm text-slate-400">
          {t("timeline.empty")}
        </p>
      ) : (
        <ol>
          {nodes.map((node, index) => (
            <TimelineRow
              key={`${node.stop_id}-${node.stop_sequence}`}
              node={node}
              isLast={index === nodes.length - 1}
              selectedDeparture={selectedDeparture}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
