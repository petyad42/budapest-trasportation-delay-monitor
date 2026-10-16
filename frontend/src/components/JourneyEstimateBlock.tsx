/**
 * JourneyEstimateBlock — the "Journey estimate" block of the redesigned
 * PredictionPanel (Requirement 2; design "Journey estimate block").
 *
 * Given a selected line + stop and a user-chosen downstream destination, this
 * block shows the expected journey duration from the selected (origin) stop to
 * that destination. The duration is derived entirely from the existing
 * `/api/timeline` (2-arg) node arrival times — no backend change (design R6
 * decision: frontend-only).
 *
 * Node contract (design "Data Models"): the backend returns `node[0]` as the
 * current stop (`is_current: true`), then downstream stops by increasing
 * `stop_sequence`. We resolve:
 *   - origin = the node with `is_current === true`, else `node[0]`, else the
 *     node whose `stop_id` matches `stopId`;
 *   - dest   = the node whose `stop_id` matches `selectedDestination`.
 * Duration (ms) = `new Date(dest.expected_arrival) - new Date(origin.expected_arrival)`,
 * converted to minutes and formatted by `formatMinutesRounded` (R2.2, R2.3).
 *
 * Graceful states:
 *   - no destination chosen (R2.4)      -> a prompt to pick a destination;
 *   - destination not on this trip (R2.5) -> a "not on this trip" note;
 *   - the query only fires when a line, stop, AND destination are all present.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { formatMinutesRounded } from "../lib/predictionText";
import { useT } from "../lib/i18n";
import { DataStatusBadge } from "./DataStatusBadge";
import type { TimelineNode } from "../lib/types";

/** Default poll interval so the estimate tracks fresh conditions. */
export const JOURNEY_POLL_MS = 30_000;

export interface JourneyEstimateBlockProps {
  /** Selected route id, or null when no line is chosen. */
  routeId: string | null;
  /** Selected (origin) stop id, or null when no stop is chosen. */
  stopId: string | null;
  /** Chosen downstream destination stop_id, or null when none is chosen. */
  selectedDestination: string | null;
  /** Override the poll interval (primarily for tests). */
  pollIntervalMs?: number;
}

/**
 * Resolve the origin node per the design contract: prefer the node flagged
 * `is_current`, then fall back to the first node, then to the node whose
 * `stop_id` matches the selected stop. Returns null for an empty timeline.
 */
export function resolveOriginNode(
  nodes: TimelineNode[],
  stopId: string | null,
): TimelineNode | null {
  if (nodes.length === 0) return null;
  const current = nodes.find((node) => node.is_current === true);
  if (current) return current;
  if (stopId != null) {
    const byStop = nodes.find((node) => node.stop_id === stopId);
    if (byStop) return byStop;
  }
  return nodes[0];
}

/**
 * Compute the whole-minute journey duration between the origin node and the
 * node matching `destinationStopId`. Returns null when the destination is not
 * among the nodes or either arrival time is unparseable (R2.5 / invalid data).
 */
export function computeJourneyMinutes(
  nodes: TimelineNode[],
  origin: TimelineNode | null,
  destinationStopId: string | null,
): number | null {
  if (origin == null || destinationStopId == null) return null;
  const dest = nodes.find((node) => node.stop_id === destinationStopId);
  if (dest == null) return null;

  const originMs = new Date(origin.expected_arrival).getTime();
  const destMs = new Date(dest.expected_arrival).getTime();
  if (Number.isNaN(originMs) || Number.isNaN(destMs)) return null;

  return (destMs - originMs) / 60000;
}

export default function JourneyEstimateBlock({
  routeId,
  stopId,
  selectedDestination,
  pollIntervalMs = JOURNEY_POLL_MS,
}: JourneyEstimateBlockProps) {
  const t = useT();
  // The journey query is gated on a line, a stop, AND a chosen destination
  // (design: "additionally gated on a chosen destination").
  const enabled =
    routeId != null && stopId != null && selectedDestination != null;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["timeline", "journey", routeId, stopId],
    queryFn: () => api.timeline(routeId as string, stopId as string),
    enabled,
    refetchInterval: pollIntervalMs,
  });

  const nodes = data?.data ?? [];
  const status = data?.status ?? null;

  const origin = resolveOriginNode(nodes, stopId);
  const destinationPresent =
    selectedDestination != null &&
    nodes.some((node) => node.stop_id === selectedDestination);
  const journeyMinutes = computeJourneyMinutes(
    nodes,
    origin,
    selectedDestination,
  );

  return (
    <section aria-label={t("journey.title")} className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-medium">{t("journey.title")}</h3>
        {enabled && status != null && <DataStatusBadge status={status} />}
      </div>

      {selectedDestination == null ? (
        // R2.4: no destination chosen — invite the rider to pick one.
        <p className="text-sm text-slate-400">
          {t("journey.prompt")}
        </p>
      ) : isLoading ? (
        <p className="text-sm text-slate-400">{t("journey.loading")}</p>
      ) : isError ? (
        <p className="text-sm text-red-600">
          {t("journey.error")}
        </p>
      ) : !destinationPresent || journeyMinutes == null ? (
        // R2.5: the chosen destination is not among this trip's timeline nodes.
        <p className="text-sm text-slate-500">
          {t("journey.notOnTrip")}
        </p>
      ) : (
        <div data-testid="journey-duration">
          <p className="text-xs uppercase tracking-wide text-slate-500">
            {t("journey.label")}
          </p>
          <p className="text-2xl font-semibold tabular-nums text-slate-800">
            {formatMinutesRounded(journeyMinutes, t)}
          </p>
        </div>
      )}
    </section>
  );
}
