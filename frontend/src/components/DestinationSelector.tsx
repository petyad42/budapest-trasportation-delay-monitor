/**
 * DestinationSelector — the downstream destination control for the journey
 * estimate (Requirement 2.1).
 *
 * This mirrors `RouteSelector`'s `StopSearchBar` scoping and reconcile pattern,
 * but constrains the option set to stops strictly *downstream* of the chosen
 * origin stop on the active direction:
 *
 *     stops.slice(stops.findIndex(s => s.stop_id === originStopId) + 1)
 *
 * so the origin and every stop before it are excluded (R2.1). The control is
 * disabled until both a line and a stop are chosen; selecting a destination
 * calls the Dashboard setter passed in via `onDestinationChange`.
 *
 * Like `RouteSelector`'s stop reconcile effect, a `useEffect` on `[stops]`
 * clears a `selectedDestination` whose `stop_id` is no longer present in the
 * active-direction stop list (for example after a direction flip), so a stale
 * destination can never linger across a direction change.
 *
 * This component is presentational and lifts its selection to the parent; it
 * does not own the `selectedDestination` state (that lives in `Dashboard`,
 * wired in task 2.2). It receives the active-direction `stops`, the current
 * `originStopId`, the `selectedDestination`, and the `onDestinationChange`
 * setter as props.
 */
import { useEffect, useMemo, useState } from "react";

import type { Stop } from "../lib/types";
import { downstreamStops } from "../lib/downstreamStops";
import { useT } from "../lib/i18n";

function matches(haystack: string, needle: string): boolean {
  return haystack.toLowerCase().includes(needle.trim().toLowerCase());
}

export interface DestinationSelectorProps {
  /** Active-direction stop list (ordered start -> end), from RouteSelector's source. */
  stops: Stop[];
  /** The chosen origin stop_id (Selected_Stop), or null when none is chosen. */
  originStopId: string | null;
  /** Currently selected destination stop, or null when none is chosen. */
  selectedDestination: Stop | null;
  /** Lifts the destination selection to the parent (Dashboard setter). */
  onDestinationChange: (stop: Stop | null) => void;
  /**
   * Disable the control until both a line and a stop are chosen. When true the
   * input is inert and shows no options (mirrors StopSearchBar's gating).
   */
  disabled: boolean;
}

export default function DestinationSelector({
  stops,
  originStopId,
  selectedDestination,
  onDestinationChange,
  disabled,
}: DestinationSelectorProps) {
  const t = useT();
  const [query, setQuery] = useState("");

  // Options are only ever the stops downstream of the origin (R2.1).
  const options = useMemo(
    () => (disabled ? [] : downstreamStops(stops, originStopId)),
    [stops, originStopId, disabled],
  );

  const filtered = useMemo(() => {
    if (disabled) return [];
    if (query.trim() === "") return options;
    return options.filter((stop) => matches(stop.name, query));
  }, [options, query, disabled]);

  // Reconcile effect (mirrors RouteSelector): when the active-direction stop
  // list changes (e.g. a direction flip), clear a selected destination whose
  // stop_id is no longer present in that list.
  useEffect(() => {
    if (selectedDestination == null) return;
    const stillPresent = stops.some(
      (s) => s.stop_id === selectedDestination.stop_id,
    );
    if (!stillPresent) {
      onDestinationChange(null);
    }
    // Only react to the stop list changing (a flip or line/stop change).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stops]);

  return (
    <div>
      <label
        htmlFor="destination-search"
        className="mb-1 block text-sm font-medium text-slate-700"
      >
        {t("destination.label")}
      </label>
      <input
        id="destination-search"
        type="text"
        role="combobox"
        aria-expanded={!disabled && filtered.length > 0}
        aria-controls="destination-options"
        autoComplete="off"
        disabled={disabled}
        placeholder={
          disabled ? t("destination.disabled") : t("destination.search")
        }
        value={disabled ? "" : query}
        onChange={(event) => setQuery(event.target.value)}
        className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
      />

      {selectedDestination && !disabled && (
        <p
          className="mt-1 text-sm text-slate-600"
          data-testid="selected-destination"
        >
          {t("destination.selected")}{" "}
          <span className="font-semibold">{selectedDestination.name}</span>
        </p>
      )}

      {!disabled && (
        <ul
          id="destination-options"
          role="listbox"
          aria-label={t("destination.ariaList")}
          className="mt-2 max-h-48 overflow-auto rounded-md border border-slate-200"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-sm text-slate-400">
              {t("destination.none")}
            </li>
          ) : (
            filtered.map((stop) => {
              const isSelected =
                selectedDestination?.stop_id === stop.stop_id;
              return (
                <li
                  key={stop.stop_id}
                  role="option"
                  aria-selected={isSelected}
                >
                  <button
                    type="button"
                    onClick={() => onDestinationChange(stop)}
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
