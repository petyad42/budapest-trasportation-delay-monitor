/**
 * ReliabilityBlock — the third stacked block of the redesigned PredictionPanel
 * (Requirements 3, 4, 5).
 *
 * It fetches the weather- and delay-adjusted prediction from
 * `/api/predict?route_id&stop_id` and reframes it as a plain-language verdict
 * using the pure `predictionText` helpers (`delayVerdict` / `adjustmentVerdict`),
 * alongside a `DataStatusBadge` derived from the response's data status
 * (R4.1–R4.6 — the badge is the single source of the live/mock/stale vocabulary,
 * and no new "live"/"estimated" wording is introduced here).
 *
 * CRITICAL availability contract (R5.4, R5.5, R5.6), mirrored from the existing
 * PredictionPanel so the states stay identical:
 *   - The verdict is rendered whenever `available === true`, regardless of
 *     `weather_used`. When `weather_used === false` the block still shows the
 *     full verdict and merely notes that the weather input was missing — it is
 *     never hidden solely because weather is missing.
 *   - Only when `available === false` does the block show the
 *     "Prediction unavailable." state.
 *
 * Graceful states (R5.2, R5.3):
 *   - loading state while the query is pending;
 *   - "Prediction is currently unavailable." on a query error.
 *
 * The query is `enabled` only when both `routeId` and `stopId` are present, and
 * the poll interval is overridable (primarily for tests). This mirrors the
 * per-block React Query pattern described in the design (each block owns its
 * own call; no shared/global query client).
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import type { Prediction } from "../lib/types";
import { adjustmentVerdict, delayVerdict } from "../lib/predictionText";
import { useT } from "../lib/i18n";
import DataStatusBadge from "./DataStatusBadge";

/** How often to re-poll the prediction so it tracks fresh conditions. */
export const RELIABILITY_POLL_MS = 30_000;

export interface ReliabilityBlockProps {
  /** Selected route id, or null when no line is chosen. */
  routeId: string | null;
  /** Selected stop id, or null when no stop is chosen. */
  stopId: string | null;
  /** Override the poll interval (primarily for tests). */
  pollIntervalMs?: number;
}

/**
 * Render the plain-language verdict for an available prediction.
 *
 * The delay verdict is derived from the current-vs-scheduled adjustment
 * (`adjustment_min`), reframed through the shared verdict helpers (R3.1–R3.3).
 */
function ReliabilityVerdict({ prediction }: { prediction: Prediction }) {
  const t = useT();
  return (
    <div className="flex flex-col gap-2" data-testid="reliability-verdict">
      <div>
        <p className="text-xs uppercase tracking-wide text-slate-500">
          {t("reliability.title")}
        </p>
        <p className="text-lg font-semibold text-slate-800">
          {delayVerdict(prediction.adjustment_min, t)}
        </p>
      </div>

      <p className="text-sm text-slate-600">
        {adjustmentVerdict(prediction.adjustment_min, t)}
      </p>

      {/* R5.5: weather may be missing; we still show the verdict and only note
          the missing weather input rather than hiding anything. */}
      {!prediction.weather_used && (
        <p className="text-xs text-amber-600" data-testid="weather-missing-note">
          {t("reliability.weatherMissing")}
        </p>
      )}
    </div>
  );
}

export default function ReliabilityBlock({
  routeId,
  stopId,
  pollIntervalMs = RELIABILITY_POLL_MS,
}: ReliabilityBlockProps) {
  const t = useT();
  const enabled = routeId != null && stopId != null;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["predict", routeId, stopId],
    queryFn: () => api.predict(routeId as string, stopId as string),
    enabled,
    refetchInterval: pollIntervalMs,
  });

  const prediction = data?.data;

  return (
    <section aria-label={t("reliability.title")} className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-medium">{t("reliability.title")}</h3>
        {/* R4.1–R4.6: data-source indicator reuses the DataStatusBadge
            vocabulary; null status renders the "Data unavailable" state. */}
        <DataStatusBadge status={data?.status ?? null} />
      </div>

      {isLoading ? (
        // R5.2: loading state while the query is pending.
        <p className="text-sm text-slate-400">{t("reliability.loading")}</p>
      ) : isError ? (
        // R5.3: query error.
        <p className="text-sm text-red-600">
          {t("reliability.error")}
        </p>
      ) : prediction == null || !prediction.available ? (
        // R5.4 / R5.6: only when transit inputs cannot be built at all.
        <p className="text-sm text-slate-500">{t("reliability.unavailable")}</p>
      ) : (
        // R5.5: render the verdict whenever it's available, even if
        // weather_used === false.
        <ReliabilityVerdict prediction={prediction} />
      )}
    </section>
  );
}
