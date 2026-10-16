/**
 * Dashboard — top-level layout that assembles every panel per the wireframe
 * (task 22). It composes:
 *
 *   Header      : DataStatusBadge (top-left) + WeatherWidget (top-right)
 *   LeftColumn  : "Local lines info" — TransitMap, LocalLinesList, ProblematicRoutesList
 *   RightColumn : "Choose a route" — RouteSelector, DeparturesPanel + PredictionPanel, ArrivalTimeline
 *
 * Selection/state (design "State management"):
 *   - `highlightedLine`  : lifted map-highlight state. LocalLinesList sets it,
 *     TransitMap draws it. This is deliberately independent from the route
 *     selector so exploring the map doesn't disturb the chosen route.
 *   - `selectedLine` / `selectedStop` : the RouteSelector's selection. These
 *     drive the departures, prediction, and timeline fetches. Selecting a new
 *     line resets the stop (handled inside RouteSelector, mirrored here).
 *
 * Refresh (R1.5, R3.4): the status and weather queries poll on an interval, and
 * the departures/prediction/timeline/problematic-routes components each poll on
 * their own interval, so the dashboard stays fresh without a full page reload.
 *
 * Degraded state (R10.3): status and weather are fetched here and passed down;
 * on error we pass `null` so DataStatusBadge/WeatherWidget render their neutral
 * fallbacks. When the backend is entirely unreachable (the status request
 * errors) we also surface a dashboard-wide degraded banner rather than leaving
 * the user with silently blank panels.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import DataStatusBadge from "./DataStatusBadge";
import QuickGuide from "./QuickGuide";
import LanguageToggle from "./LanguageToggle";
import WeatherWidget from "./WeatherWidget";
import TransitMap from "./TransitMap";
import LocalLinesList from "./LocalLinesList";
import ProblematicRoutesList from "./ProblematicRoutesList";
import RouteSelector from "./RouteSelector";
import DestinationSelector from "./DestinationSelector";
import DeparturesPanel from "./DeparturesPanel";
import PredictionPanel from "./PredictionPanel";
import ArrivalTimeline from "./ArrivalTimeline";

import { api } from "../lib/api";
import { BUDAPEST_DEFAULT } from "../lib/config";
import { useGeolocation } from "../lib/useGeolocation";
import { useT } from "../lib/i18n";
import type { Departure, Line, Stop } from "../lib/types";

/** How often to re-poll the global data status (R1.5, R10.2). */
export const STATUS_POLL_MS = 30_000;
/** How often to re-poll current weather (R8). */
export const WEATHER_POLL_MS = 300_000;

export interface DashboardProps {
  /** Override poll intervals (primarily for tests). */
  statusPollMs?: number;
  weatherPollMs?: number;
}

export default function Dashboard({
  statusPollMs = STATUS_POLL_MS,
  weatherPollMs = WEATHER_POLL_MS,
}: DashboardProps) {
  const t = useT();
  // Lifted map-highlight state: LocalLinesList sets it, TransitMap draws it (R2.4).
  const [highlightedLine, setHighlightedLine] = useState<Line | null>(null);

  // Route-selector state: drives departures / prediction / timeline (R4, R5, R7).
  const [selectedLine, setSelectedLine] = useState<Line | null>(null);
  const [selectedStop, setSelectedStop] = useState<Stop | null>(null);

  // The departure the user picked in the departures list; drives the arrival
  // timeline's head node. Reset whenever the route or stop changes so a stale
  // trip never colors a different stop's timeline.
  const [selectedDeparture, setSelectedDeparture] = useState<Departure | null>(
    null,
  );

  // The downstream destination the user picked for the journey estimate (R2.1).
  // Lifted here with the SAME reset discipline as `selectedDeparture`: cleared
  // on any line change and any stop change (a direction flip cascades through
  // `handleStopChange`), so a stale destination never leaks across selections.
  const [selectedDestination, setSelectedDestination] = useState<Stop | null>(
    null,
  );

  // The active-direction ordered stop list, lifted from RouteSelector. It
  // scopes the destination selector's downstream options (R2.1).
  const [directionStops, setDirectionStops] = useState<Stop[]>([]);

  // Weather is location-aware; reuse the same geolocation the map uses so the
  // widget reflects the user's area (falls back to Budapest — R2.5).
  const { coords } = useGeolocation();
  const weatherCoords = coords ?? BUDAPEST_DEFAULT;

  // Global data status (green=realtime / orange=mock). On error we pass null so
  // the badge renders its neutral "unavailable" state (R10.1, R10.3).
  const statusQuery = useQuery({
    queryKey: ["status"],
    // The badge needs the envelope's data_status (source/stale), which the API
    // client exposes as `.status`. The `.data` payload is the per-source
    // breakdown ({ realtime, catalog }) and has no `source` field, so reading
    // it would leave the badge permanently on "mock".
    queryFn: async () => (await api.status()).status,
    refetchInterval: statusPollMs,
  });

  // Current weather; null on failure so the widget shows its fallback (R8.3).
  const weatherQuery = useQuery({
    queryKey: ["weather", weatherCoords.lat, weatherCoords.lon],
    queryFn: async () =>
      (await api.weather(weatherCoords.lat, weatherCoords.lon)).data,
    refetchInterval: weatherPollMs,
  });

  const status = statusQuery.isError ? null : (statusQuery.data ?? null);
  const weather = weatherQuery.isError ? null : (weatherQuery.data ?? null);

  // Treat the backend as unreachable when the status request itself failed
  // (network/5xx). This drives the dashboard-wide degraded banner (R10.3).
  const backendUnreachable = statusQuery.isError;

  const routeId = selectedLine?.route_id ?? null;
  const stopId = selectedStop?.stop_id ?? null;

  function handleLineChange(line: Line | null) {
    // A new line invalidates the previously chosen stop (R4.2/R4.5), the
    // departure selected for the old route/stop, and the chosen destination.
    if (line?.route_id !== selectedLine?.route_id) {
      setSelectedStop(null);
      setSelectedDeparture(null);
      setSelectedDestination(null);
    }
    setSelectedLine(line);
  }

  function handleStopChange(stop: Stop | null) {
    // Changing the stop invalidates the departure chosen for the old stop and
    // the destination (which must be downstream of the new stop). A direction
    // flip clears the stop too, cascading through here, so this also covers the
    // flip case.
    setSelectedDeparture(null);
    setSelectedDestination(null);
    setSelectedStop(stop);
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="relative z-[2000] flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 py-3">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-semibold">{t("app.title")}</h1>
          {/* Top-left: data-status indicator (R10.1). */}
          <DataStatusBadge status={status} />
          {/* Hover/focus help: a short walkthrough of how to use the site. */}
          <QuickGuide />
        </div>
        {/* Top-right: current weather (R8) + language switch. */}
        <div className="flex items-center gap-3">
          <WeatherWidget weather={weather} />
          <LanguageToggle />
        </div>
      </header>

      {backendUnreachable && (
        <div
          role="alert"
          className="border-b border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700"
        >
          {t("banner.backendUnreachable")}
        </div>
      )}

      <main className="mx-auto grid max-w-7xl grid-cols-1 gap-4 p-4 lg:grid-cols-2">
        {/* Left column: "Local lines info". */}
        <section
          aria-label={t("section.localLinesInfo")}
          className="flex flex-col gap-4 rounded-lg border border-slate-200 bg-white p-4"
        >
          <h2 className="font-medium">{t("section.localLinesInfo")}</h2>
          <TransitMap highlightedLine={highlightedLine} />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <LocalLinesList
              selectedLine={highlightedLine}
              onSelectLine={setHighlightedLine}
            />
            <ProblematicRoutesList />
          </div>
        </section>

        {/* Right column: "Choose a route". */}
        <section
          aria-label={t("section.chooseRoute")}
          className="flex flex-col gap-4 rounded-lg border border-slate-200 bg-white p-4"
        >
          <h2 className="font-medium">{t("section.chooseRoute")}</h2>

          <RouteSelector
            selectedLine={selectedLine}
            selectedStop={selectedStop}
            onLineChange={handleLineChange}
            onStopChange={handleStopChange}
            onStopsChange={setDirectionStops}
          />

          {/* Destination selector: downstream-only options scoped to the active
              direction's stops, disabled until both a line and a stop are
              chosen (R2.1). Its selection feeds the journey estimate. */}
          <DestinationSelector
            stops={directionStops}
            originStopId={stopId}
            selectedDestination={selectedDestination}
            onDestinationChange={setSelectedDestination}
            disabled={routeId == null || stopId == null}
          />

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <DeparturesPanel
              routeId={routeId}
              stopId={stopId}
              selectedDeparture={selectedDeparture}
              onSelectDeparture={setSelectedDeparture}
            />
            <PredictionPanel
              routeId={routeId}
              stopId={stopId}
              selectedDestination={selectedDestination?.stop_id ?? null}
            />
          </div>

          <ArrivalTimeline
            routeId={routeId}
            stopId={stopId}
            selectedDeparture={selectedDeparture}
          />
        </section>
      </main>
    </div>
  );
}
