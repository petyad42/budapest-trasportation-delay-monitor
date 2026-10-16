/**
 * ProblematicRoutesList — the "Top 5 most problematic routes" panel (Requirement 3).
 *
 * Fetches the ranked problem scores from `/api/problematic-routes?limit=5` and
 * renders each route with:
 *   - its route identifier (short_name) (R3.3),
 *   - its mode, color-coded via the shared colors module (R3.3, R2.2),
 *   - its current delay metric in a human-readable format (R3.3).
 *
 * The backend already returns routes ranked by problem score (R3.1, R3.2) and
 * only those with measurable delays (R3.5), so the component renders the list
 * as-is. React Query polls on an interval so the ranking refreshes as the
 * underlying delay data changes, without a full page reload (R3.4).
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { colorForMode } from "../lib/colors";
import type { ProblemScore } from "../lib/types";
import { useT, type Translate } from "../lib/i18n";

/** How often to re-poll the ranking so it tracks fresh delay data (R3.4). */
export const PROBLEMATIC_ROUTES_POLL_MS = 30_000;

/** Number of routes requested/displayed (R3.2). */
export const PROBLEMATIC_ROUTES_LIMIT = 5;

/** Human-readable delay metric, e.g. `4.2 min late` / `on time` (R3.3). */
export function formatDelayMetric(avgDelayMin: number, t?: Translate): string {
  if (avgDelayMin <= 0) return t ? t("departures.onTime") : "on time";
  const rounded = Math.round(avgDelayMin * 10) / 10;
  return t ? t("pt.metric.late", { n: rounded }) : `${rounded} min late`;
}

/** Translated mode label, e.g. `tram` -> `Villamos` (hu) / `Tram` (en). */
function modeLabel(mode: ProblemScore["mode"], t: Translate): string {
  return t(`mode.${mode}`);
}

function ProblematicRouteRow({
  route,
  rank,
  t,
}: {
  route: ProblemScore;
  rank: number;
  t: Translate;
}) {
  const color = colorForMode(route.mode);
  return (
    <li className="flex items-center gap-3 py-2">
      <span className="w-4 shrink-0 text-sm tabular-nums text-slate-400">
        {rank}
      </span>
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span
          aria-hidden="true"
          className="inline-block h-3 w-3 shrink-0 rounded-full"
          style={{ backgroundColor: color }}
        />
        <span className="truncate font-medium">{route.short_name}</span>
        <span className="shrink-0 text-xs uppercase tracking-wide text-slate-500">
          {modeLabel(route.mode, t)}
        </span>
      </div>
      <span className="shrink-0 text-sm tabular-nums text-slate-700">
        {formatDelayMetric(route.avg_delay_min, t)}
      </span>
    </li>
  );
}

export interface ProblematicRoutesListProps {
  /** Override the poll interval (primarily for tests). */
  pollIntervalMs?: number;
}

export default function ProblematicRoutesList({
  pollIntervalMs = PROBLEMATIC_ROUTES_POLL_MS,
}: ProblematicRoutesListProps) {
  const t = useT();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["problematic-routes", PROBLEMATIC_ROUTES_LIMIT],
    queryFn: () => api.problematicRoutes(PROBLEMATIC_ROUTES_LIMIT),
    refetchInterval: pollIntervalMs,
  });

  const routes = data?.data ?? [];

  return (
    <section aria-label={t("problematic.title")}>
      <h3 className="mb-2 font-medium">{t("problematic.title")}</h3>

      {isLoading ? (
        <p className="text-sm text-slate-400">{t("problematic.loading")}</p>
      ) : isError ? (
        <p className="text-sm text-red-600">
          {t("problematic.error")}
        </p>
      ) : routes.length === 0 ? (
        // Fewer than one route with measurable delays (R3.5).
        <p className="text-sm text-slate-400">{t("problematic.empty")}</p>
      ) : (
        <ol className="divide-y divide-slate-100">
          {routes.map((route, index) => (
            <ProblematicRouteRow
              key={route.route_id}
              route={route}
              rank={index + 1}
              t={t}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
