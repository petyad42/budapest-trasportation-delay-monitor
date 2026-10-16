/**
 * PredictionPanel — the prediction column of the "Choose a route" view,
 * redesigned into three stacked, rider-oriented blocks (design "Three-block
 * composition"; Requirements 1–5).
 *
 * Given a selected `route_id` and `stop_id` (plus an optional downstream
 * `selectedDestination`), the panel renders, top to bottom:
 *   1. Next ride        (NextRideBlock)        — R1, R4
 *   2. Journey estimate (JourneyEstimateBlock) — R2
 *   3. Reliability      (ReliabilityBlock)      — R3, R4, R5
 *
 * Each block owns its own data fetch, polling, and graceful states (loading /
 * error / unavailable / empty), so this panel stays a thin composition layer.
 *
 * When no line or stop is chosen (R5.1) the panel shows a single
 * select-a-line-and-stop prompt and mounts NONE of the blocks, so no requests
 * fire until both ids are present.
 */
import type { Departure } from "../lib/types";
import NextRideBlock from "./NextRideBlock";
import JourneyEstimateBlock from "./JourneyEstimateBlock";
import ReliabilityBlock from "./ReliabilityBlock";
import { useT } from "../lib/i18n";

/**
 * Default poll interval for the panel's blocks (R1.5). Retained as a named
 * export for backward compatibility with existing importers/tests.
 */
export const PREDICTION_POLL_MS = 30_000;

/**
 * Round to one decimal and format as `N min`.
 *
 * @deprecated Superseded by the pure helpers in `lib/predictionText.ts`
 * (`formatMinutesRounded`), which the blocks use. Retained only for backward
 * compatibility with existing importers; new code should not use it.
 */
export function formatMinutes(min: number): string {
  const rounded = Math.round(min * 10) / 10;
  return `${rounded} min`;
}

/**
 * Signed adjustment, e.g. `+2.5 min slower`, `-1 min faster`, `no change`.
 *
 * @deprecated Superseded by `adjustmentVerdict` in `lib/predictionText.ts`,
 * which the Reliability block uses. Retained only for backward compatibility
 * with existing importers; new code should not use it.
 */
export function formatAdjustment(adjustmentMin: number): string {
  const rounded = Math.round(adjustmentMin * 10) / 10;
  if (rounded === 0) return "no change vs scheduled";
  if (rounded > 0) return `+${rounded} min slower than scheduled`;
  return `${rounded} min faster than scheduled`;
}

export interface PredictionPanelProps {
  /** Selected route id, or null when no line is chosen. */
  routeId: string | null;
  /** Selected (origin) stop id, or null when no stop is chosen. */
  stopId: string | null;
  /**
   * Downstream destination stop_id for the journey estimate; null when none is
   * chosen. Threaded from the Dashboard's lifted `selectedDestination` state
   * and forwarded to the Journey estimate block.
   */
  selectedDestination: string | null;
  /**
   * Optional: the departure the rider selected, if useful for aligning the
   * trip. Accepted so the Dashboard can thread it without a breaking change;
   * the current blocks derive the next ride from `/api/departures` directly.
   */
  selectedDeparture?: Departure | null;
  /** Override the poll interval for every block (primarily for tests). */
  pollIntervalMs?: number;
}

export default function PredictionPanel({
  routeId,
  stopId,
  selectedDestination,
  pollIntervalMs = PREDICTION_POLL_MS,
}: PredictionPanelProps) {
  const t = useT();
  const enabled = routeId != null && stopId != null;

  // R5.1: without both a line and a stop, show a single prompt and mount none
  // of the blocks so no requests fire until both ids are present.
  if (!enabled) {
    return (
      <section aria-label={t("prediction.title")} className="flex flex-col">
        <h3 className="mb-2 font-medium">{t("prediction.title")}</h3>
        <p className="text-sm text-slate-400">
          {t("prediction.prompt")}
        </p>
      </section>
    );
  }

  // Otherwise render the three blocks in order. Each block owns its own fetch,
  // polling, and graceful states (loading / error / unavailable / empty).
  return (
    <section aria-label={t("prediction.title")} className="flex flex-col gap-4">
      <NextRideBlock
        routeId={routeId}
        stopId={stopId}
        pollIntervalMs={pollIntervalMs}
      />
      <JourneyEstimateBlock
        routeId={routeId}
        stopId={stopId}
        selectedDestination={selectedDestination}
        pollIntervalMs={pollIntervalMs}
      />
      <ReliabilityBlock
        routeId={routeId}
        stopId={stopId}
        pollIntervalMs={pollIntervalMs}
      />
    </section>
  );
}
