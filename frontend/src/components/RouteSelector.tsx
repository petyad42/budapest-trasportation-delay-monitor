/**
 * RouteSelector — the "Choose a route" control (Requirement 4).
 *
 * Composes three controls:
 *   - LineSearchBar    : search and select from all available lines (4.1).
 *   - DirectionRow     : the active direction's start terminus and end terminus
 *                        with a clickable flip arrow between them (4.3). The
 *                        arrow switches the active direction (4.4); it is
 *                        disabled for single-direction lines (4.8, 4.10).
 *   - StopSearchBar    : disabled/empty until a line is chosen (4.12); once a
 *                        line is selected it is scoped to the *active*
 *                        direction's stops only, ordered start -> end with no
 *                        duplicates (4.3, 4.9).
 *
 * On a direction flip the currently selected stop is preserved when the same
 * stop_id exists in the opposite direction, and cleared otherwise (4.7, 4.8).
 *
 * The selection is lifted to the parent through `onLineChange`/`onStopChange`
 * so downstream views (departures, prediction, timeline) react to the same
 * `selectedLine`/`selectedStop`.
 *
 * Data is fetched with React Query:
 *   - `/api/lines`                                   -> available lines
 *   - `/api/lines/{route_id}/stops?direction={dir}`  -> LineDirections for the
 *     selected line + active direction (stops + termini + available directions)
 * The stops query is disabled until a line is selected, so no request is made
 * (and the stop bar stays disabled) while there is no line.
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import type { Direction, Line, LineDirections, Stop } from "../lib/types";
import { colorForMode } from "../lib/colors";
import { useT } from "../lib/i18n";

export interface RouteSelectorProps {
  /** Currently selected line, or null when none is chosen. */
  selectedLine: Line | null;
  /** Currently selected stop, or null when none is chosen. */
  selectedStop: Stop | null;
  /**
   * Called when the selected line changes. Because a new line invalidates any
   * previously selected stop, the parent should also clear the stop when this
   * fires; `RouteSelector` additionally calls `onStopChange(null)` itself so
   * the reset happens even if the parent forgets.
   */
  onLineChange: (line: Line | null) => void;
  /** Called when the selected stop changes. */
  onStopChange: (stop: Stop | null) => void;
  /**
   * Optional: lifts the *active-direction* ordered stop list (start -> end,
   * deduped) up to the parent whenever it changes. This is the same list that
   * scopes the stop selector, and it is what the destination selector needs to
   * compute downstream-only options (R2.1). Optional and backward-compatible:
   * callers that don't need the stop list can omit it.
   */
  onStopsChange?: (stops: Stop[]) => void;
}

function matches(haystack: string, needle: string): boolean {
  return haystack.toLowerCase().includes(needle.trim().toLowerCase());
}

/**
 * LineSearchBar — searches the available lines and lets the user pick one.
 * Filtering matches against both the short name and the route id.
 */
function LineSearchBar({
  lines,
  selectedLine,
  onSelect,
  isLoading,
  isError,
}: {
  lines: Line[];
  selectedLine: Line | null;
  onSelect: (line: Line) => void;
  isLoading: boolean;
  isError: boolean;
}) {
  const t = useT();
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    if (query.trim() === "") return lines;
    return lines.filter(
      (line) =>
        matches(line.short_name, query) || matches(line.route_id, query),
    );
  }, [lines, query]);

  return (
    <div>
      <label
        htmlFor="line-search"
        className="mb-1 block text-sm font-medium text-slate-700"
      >
        {t("route.lineLabel")}
      </label>
      <input
        id="line-search"
        type="text"
        role="combobox"
        aria-expanded={filtered.length > 0}
        aria-controls="line-options"
        autoComplete="off"
        placeholder={t("route.searchLines")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
      />

      {selectedLine && (
        <p className="mt-1 text-sm text-slate-600" data-testid="selected-line">
          {t("route.selectedLine")}{" "}
          <span className="font-semibold">{selectedLine.short_name}</span>
        </p>
      )}

      {isLoading && (
        <p className="mt-2 text-sm text-slate-400">{t("route.loadingLines")}</p>
      )}
      {isError && (
        <p className="mt-2 text-sm text-red-600">{t("route.linesError")}</p>
      )}

      {!isLoading && !isError && (
        <ul
          id="line-options"
          role="listbox"
          aria-label={t("route.linesAvailable")}
          className="mt-2 max-h-48 overflow-auto rounded-md border border-slate-200"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-sm text-slate-400">
              {t("route.noLines")}
            </li>
          ) : (
            filtered.map((line) => {
              const isSelected = selectedLine?.route_id === line.route_id;
              return (
                <li key={line.route_id} role="option" aria-selected={isSelected}>
                  <button
                    type="button"
                    onClick={() => onSelect(line)}
                    className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-slate-50 ${
                      isSelected ? "bg-slate-100 font-semibold" : ""
                    }`}
                  >
                    <span
                      aria-hidden="true"
                      className="inline-block h-3 w-3 rounded-full"
                      style={{
                        backgroundColor: colorForMode(line.mode, line.color),
                      }}
                    />
                    <span>{line.short_name}</span>
                    <span className="text-xs text-slate-400">{line.mode}</span>
                  </button>
                </li>
              );
            })
          )}
        </ul>
      )}
    </div>
  );
}

/**
 * DirectionRow — start terminus · flip arrow · end terminus (R4.3, R4.4).
 *
 * The arrow flips the active direction. It is disabled when the line serves
 * only a single direction (R4.8, R4.10). Terminus names come from the active
 * direction so a flip visibly swaps which end is shown as start vs end.
 */
function DirectionRow({
  active,
  canFlip,
  onFlip,
}: {
  active: Direction | null;
  canFlip: boolean;
  onFlip: () => void;
}) {
  const t = useT();
  const start = active?.start_terminus ?? "—";
  const end = active?.end_terminus ?? "—";

  return (
    <div
      className="flex items-center justify-between gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2"
      data-testid="direction-row"
    >
      <span
        className="min-w-0 flex-1 truncate text-sm font-medium text-slate-700"
        data-testid="direction-start"
        title={start}
      >
        {start}
      </span>
      <button
        type="button"
        onClick={onFlip}
        disabled={!canFlip}
        aria-label={t("route.flip")}
        title={canFlip ? t("route.flip") : t("route.flipDisabled")}
        className="shrink-0 rounded-md border border-slate-300 bg-white px-2 py-1 text-base leading-none text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-40"
      >
        <span aria-hidden="true">⇄</span>
      </button>
      <span
        className="min-w-0 flex-1 truncate text-right text-sm font-medium text-slate-700"
        data-testid="direction-end"
        title={end}
      >
        {end}
      </span>
    </div>
  );
}

/**
 * StopSearchBar — scoped to the active direction's stops.
 *
 * When no line is selected the input is disabled and no options are shown
 * (Requirement 4.12). Once a line is selected the options come exclusively from
 * the active direction's `/stops` response (Requirements 4.3, 4.9).
 */
function StopSearchBar({
  stops,
  selectedStop,
  onSelect,
  disabled,
  isLoading,
  isError,
}: {
  stops: Stop[];
  selectedStop: Stop | null;
  onSelect: (stop: Stop) => void;
  disabled: boolean;
  isLoading: boolean;
  isError: boolean;
}) {
  const t = useT();
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    if (disabled) return [];
    if (query.trim() === "") return stops;
    return stops.filter((stop) => matches(stop.name, query));
  }, [stops, query, disabled]);

  return (
    <div>
      <label
        htmlFor="stop-search"
        className="mb-1 block text-sm font-medium text-slate-700"
      >
        {t("route.stopLabel")}
      </label>
      <input
        id="stop-search"
        type="text"
        role="combobox"
        aria-expanded={!disabled && filtered.length > 0}
        aria-controls="stop-options"
        autoComplete="off"
        disabled={disabled}
        placeholder={disabled ? t("route.selectStopFirst") : t("route.searchStops")}
        value={disabled ? "" : query}
        onChange={(event) => setQuery(event.target.value)}
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
      />

      {selectedStop && !disabled && (
        <p className="mt-1 text-sm text-slate-600" data-testid="selected-stop">
          {t("route.selectedStop")}{" "}
          <span className="font-semibold">{selectedStop.name}</span>
        </p>
      )}

      {!disabled && isLoading && (
        <p className="mt-2 text-sm text-slate-400">{t("route.loadingStops")}</p>
      )}
      {!disabled && isError && (
        <p className="mt-2 text-sm text-red-600">{t("route.stopsError")}</p>
      )}

      {!disabled && !isLoading && !isError && (
        <ul
          id="stop-options"
          role="listbox"
          aria-label={t("route.stopsList")}
          className="mt-2 max-h-48 overflow-auto rounded-md border border-slate-200"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-sm text-slate-400">
              {t("route.noStops")}
            </li>
          ) : (
            filtered.map((stop) => {
              const isSelected = selectedStop?.stop_id === stop.stop_id;
              return (
                <li key={stop.stop_id} role="option" aria-selected={isSelected}>
                  <button
                    type="button"
                    onClick={() => onSelect(stop)}
                    className={`w-full px-3 py-2 text-left text-sm hover:bg-slate-50 ${
                      isSelected ? "bg-slate-100 font-semibold" : ""
                    }`}
                  >
                    {stop.name}
                  </button>
                </li>
              );
            })
          )}
        </ul>
      )}
    </div>
  );
}

export default function RouteSelector({
  selectedLine,
  selectedStop,
  onLineChange,
  onStopChange,
  onStopsChange,
}: RouteSelectorProps) {
  const linesQuery = useQuery({
    queryKey: ["lines"],
    queryFn: async () => (await api.lines()).data,
  });

  const routeId = selectedLine?.route_id ?? null;

  // Active travel direction (GTFS direction_id). Null means "let the backend
  // pick its default" (the lowest available direction). Reset when the line
  // changes so a fresh line always starts on its default direction.
  const [activeDirection, setActiveDirection] = useState<string | null>(null);

  useEffect(() => {
    setActiveDirection(null);
  }, [routeId]);

  const stopsQuery = useQuery({
    queryKey: ["lines", routeId, "stops", activeDirection],
    queryFn: async () =>
      (await api.stopsForLine(routeId as string, activeDirection ?? undefined))
        .data,
    // No line selected -> no request; keeps the stop bar disabled/empty (4.12).
    enabled: routeId != null,
  });

  const lineDirections: LineDirections | null = stopsQuery.data ?? null;
  const stops = useMemo(() => lineDirections?.stops ?? [], [lineDirections]);
  const directions = useMemo(
    () => lineDirections?.directions ?? [],
    [lineDirections],
  );
  const activeDirectionId =
    lineDirections?.active_direction ?? activeDirection ?? "";
  const activeDirectionObj =
    directions.find((d) => d.direction_id === activeDirectionId) ??
    directions[0] ??
    null;
  const canFlip = directions.length > 1;

  function handleLineSelect(line: Line) {
    // Selecting a (different) line resets the stop (Requirement 4.3/4.12).
    if (line.route_id !== selectedLine?.route_id) {
      onStopChange(null);
    }
    onLineChange(line);
  }

  function handleFlip() {
    if (!canFlip) return;
    const opposite = directions.find(
      (d) => d.direction_id !== activeDirectionId,
    );
    if (!opposite) return;

    // Preserve the selected stop if it exists in the opposite direction's
    // stops; otherwise clear it (R4.7, R4.8). The opposite direction's stop
    // list is fetched by the direction-keyed query below, so we optimistically
    // decide here using the stop_id and let the refetch confirm membership.
    //
    // We cannot see the opposite direction's stops until the query refetches,
    // so defer the preserve/clear decision to an effect that runs once the new
    // direction's stops arrive (see `reconcileStopOnFlip`). Switch direction
    // first.
    setActiveDirection(opposite.direction_id);
  }

  // After a flip, the stops query refetches for the new direction. Once the new
  // stop list is in, reconcile the selected stop: keep it if its stop_id is
  // present in the new direction, otherwise clear it (R4.7, R4.8).
  useEffect(() => {
    if (selectedStop == null) return;
    if (stopsQuery.isLoading || stopsQuery.isError) return;
    if (stops.length === 0) return;
    const stillPresent = stops.some(
      (s) => s.stop_id === selectedStop.stop_id,
    );
    if (!stillPresent) {
      onStopChange(null);
    }
    // Only react to the stop list changing (a flip or line change refetch).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stops]);

  // Lift the active-direction stop list to the parent so the destination
  // selector can scope its options to downstream stops (R2.1). Fires whenever
  // the list changes (line change / direction flip refetch).
  useEffect(() => {
    onStopsChange?.(stops);
    // Only react to the stop list changing; `onStopsChange` is assumed stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stops]);

  const lineSelected = selectedLine != null;

  return (
    <div className="flex flex-col gap-4">
      <LineSearchBar
        lines={linesQuery.data ?? []}
        selectedLine={selectedLine}
        onSelect={handleLineSelect}
        isLoading={linesQuery.isLoading}
        isError={linesQuery.isError}
      />
      {lineSelected && (
        <DirectionRow
          active={activeDirectionObj}
          canFlip={canFlip}
          onFlip={handleFlip}
        />
      )}
      <StopSearchBar
        stops={stops}
        selectedStop={selectedStop}
        onSelect={onStopChange}
        disabled={!lineSelected}
        isLoading={stopsQuery.isLoading}
        isError={stopsQuery.isError}
      />
    </div>
  );
}
