/**
 * LocalLinesList — the "local lines" list shown beside/below the map
 * (Requirements 2.3, 2.4).
 *
 * Fetches the available lines from `/api/lines` and renders them as a
 * selectable list, each color-coded by mode via the shared colors module
 * (R2.2). Clicking a line lifts it to the parent through `onSelectLine`, which
 * drives the map highlight (the parent passes the selected line back down to
 * `TransitMap` as `highlightedLine`, which then fetches the route shape/stops
 * and draws them — R2.4).
 *
 * Selection is fully controlled: the currently highlighted line is passed in as
 * `selectedLine`. Clicking the already-selected line toggles the highlight off
 * (passes `null`) so the user can clear it.
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "../lib/api";
import { colorForMode } from "../lib/colors";
import type { Line } from "../lib/types";
import { useT, type Translate } from "../lib/i18n";

/** Translated mode label, e.g. `tram` -> `Villamos` (hu) / `Tram` (en). */
function modeLabel(mode: Line["mode"], t: Translate): string {
  return t(`mode.${mode}`);
}

export interface LocalLinesListProps {
  /** The currently highlighted line (lifted state), or null when none. */
  selectedLine: Line | null;
  /**
   * Called when the user clicks a line. Passes the clicked line, or `null` when
   * the user clicks the already-selected line to clear the highlight.
   */
  onSelectLine: (line: Line | null) => void;
}

function LineRow({
  line,
  isSelected,
  onSelect,
  t,
}: {
  line: Line;
  isSelected: boolean;
  onSelect: (line: Line | null) => void;
  t: Translate;
}) {
  const color = colorForMode(line.mode, line.color);
  return (
    <li role="option" aria-selected={isSelected}>
      <button
        type="button"
        onClick={() => onSelect(isSelected ? null : line)}
        className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-slate-50 ${
          isSelected ? "bg-slate-100 font-semibold" : ""
        }`}
      >
        <span
          aria-hidden="true"
          className="inline-block h-3 w-3 shrink-0 rounded-full"
          style={{ backgroundColor: color }}
        />
        <span className="truncate font-medium">{line.short_name}</span>
        <span className="shrink-0 text-xs uppercase tracking-wide text-slate-500">
          {modeLabel(line.mode, t)}
        </span>
      </button>
    </li>
  );
}

export default function LocalLinesList({
  selectedLine,
  onSelectLine,
}: LocalLinesListProps) {
  const t = useT();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["lines"],
    queryFn: async () => (await api.lines()).data,
  });

  const lines = data ?? [];

  return (
    <section aria-label={t("lines.ariaList")}>
      <h3 className="mb-2 font-medium">{t("lines.title")}</h3>

      {isLoading ? (
        <p className="text-sm text-slate-400">{t("lines.loading")}</p>
      ) : isError ? (
        <p className="text-sm text-red-600">
          {t("lines.error")}
        </p>
      ) : lines.length === 0 ? (
        <p className="text-sm text-slate-400">{t("lines.empty")}</p>
      ) : (
        <ul
          role="listbox"
          aria-label={t("lines.ariaList")}
          className="max-h-64 divide-y divide-slate-100 overflow-auto rounded-md border border-slate-200"
        >
          {lines.map((line) => (
            <LineRow
              key={line.route_id}
              line={line}
              isSelected={selectedLine?.route_id === line.route_id}
              onSelect={onSelectLine}
              t={t}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
