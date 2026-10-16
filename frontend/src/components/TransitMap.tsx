/**
 * TransitMap — Leaflet map of the user's area with color-coded nearby stops.
 *
 * Responsibilities (Requirements 2.1, 2.2, 2.5):
 *  - Render a Leaflet map with free OpenStreetMap tiles.
 *  - Center on the user's geolocation, falling back to the Budapest default
 *    coordinates when geolocation is unavailable or denied (R2.5).
 *  - Plot nearby stops (from `/api/stops/near`) as color-coded circle markers,
 *    using the shared mode/color module so the map stays consistent with the
 *    lists and legend (R2.2, design "Property 8").
 *
 * The nearby-stops endpoint returns bare `Stop` objects with no mode, so a
 * stop's mode is supplied via the optional `stopModes` map (populated by the
 * lines/stops data the parent already holds). Stops with no known mode fall
 * back to a neutral color rather than being hidden.
 *
 * When the parent lifts a highlighted line (via `highlightedLine`), the map
 * fetches that line's route shape (`/api/lines/{id}/shape`) and its stops
 * (`/api/lines/{id}/stops`) and draws the polyline plus emphasized stop markers
 * in the line's mode color (Requirement 2.4).
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  Popup,
  TileLayer,
  Tooltip,
} from "react-leaflet";

import { api } from "../lib/api";
import type { Line, Stop } from "../lib/types";
import type { Mode } from "../lib/colors";
import { colorForMode } from "../lib/colors";
import { DEFAULT_MAP_ZOOM, NEARBY_STOPS_RADIUS_M } from "../lib/config";
import { useGeolocation } from "../lib/useGeolocation";

const OSM_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

/** Neutral color for a stop whose serving mode is unknown. */
const UNKNOWN_STOP_COLOR = "#94A3B8"; // slate-400

/** Marker color for the user's / map center location. */
const USER_MARKER_COLOR = "#1D4ED8"; // blue-700

export interface StopModeInfo {
  mode: Mode;
  /** Official GTFS route_color, only used to color metro stops. */
  routeColor?: string | null;
}

export interface TransitMapProps {
  /** Radius (metres) to query nearby stops within. Defaults to 800 m. */
  radiusM?: number;
  /** Initial map zoom. Defaults to a city-block level. */
  zoom?: number;
  /**
   * Optional per-stop mode info so markers can be colored by mode (R2.2).
   * Keyed by `stop_id`. Stops absent from this map render in a neutral color.
   */
  stopModes?: Record<string, StopModeInfo>;
  /**
   * Line to highlight on the map (R2.4). When set, the map fetches the line's
   * route shape and stops and draws the polyline plus emphasized stop markers
   * in the line's mode color. Null clears any highlight.
   */
  highlightedLine?: Line | null;
  /** Fixed map height. Defaults to a sensible dashboard size. */
  heightClass?: string;
}

/** Resolve the display color for a single stop from the mode map. */
function colorForStop(
  stop: Stop,
  stopModes: Record<string, StopModeInfo> | undefined,
): string {
  const info = stopModes?.[stop.stop_id];
  if (!info) return UNKNOWN_STOP_COLOR;
  return colorForMode(info.mode, info.routeColor);
}

export default function TransitMap({
  radiusM = NEARBY_STOPS_RADIUS_M,
  zoom = DEFAULT_MAP_ZOOM,
  stopModes,
  highlightedLine = null,
  heightClass = "h-80",
}: TransitMapProps) {
  const { coords, usedFallback } = useGeolocation();

  const {
    data: stops,
    isError,
  } = useQuery({
    queryKey: ["stops-near", coords.lat, coords.lon, radiusM],
    queryFn: async () => {
      const result = await api.stopsNear(coords.lat, coords.lon, radiusM);
      return result.data;
    },
  });

  const highlightRouteId = highlightedLine?.route_id ?? null;

  // Route polyline for the highlighted line (R2.4).
  const { data: highlightShape } = useQuery({
    queryKey: ["line-shape", highlightRouteId],
    queryFn: async () => {
      const result = await api.lineShape(highlightRouteId as string);
      return result.data;
    },
    enabled: highlightRouteId != null,
  });

  // Stops belonging to the highlighted line, emphasized on the map (R2.4).
  const { data: highlightStops } = useQuery({
    queryKey: ["line-stops", highlightRouteId],
    queryFn: async () => {
      const result = await api.stopsForLine(highlightRouteId as string);
      // `/stops` now returns direction-aware metadata; the map only needs the
      // active direction's stop list (R2.4).
      return result.data?.stops ?? [];
    },
    enabled: highlightRouteId != null,
  });

  const highlightColor = highlightedLine
    ? colorForMode(highlightedLine.mode, highlightedLine.color)
    : undefined;

  const center = useMemo<[number, number]>(
    () => [coords.lat, coords.lon],
    [coords.lat, coords.lon],
  );

  return (
    <div className="space-y-2">
      <div
        className={`${heightClass} w-full overflow-hidden rounded-md border border-slate-200`}
      >
        <MapContainer
          key={`${coords.lat},${coords.lon}`}
          center={center}
          zoom={zoom}
          scrollWheelZoom={false}
          className="h-full w-full"
          aria-label="Map of nearby transit stops"
        >
          <TileLayer url={OSM_TILE_URL} attribution={OSM_ATTRIBUTION} />

          {/* Current location / map center marker. */}
          <CircleMarker
            center={center}
            radius={7}
            pathOptions={{
              color: "#FFFFFF",
              weight: 2,
              fillColor: USER_MARKER_COLOR,
              fillOpacity: 1,
            }}
          >
            <Tooltip>
              {usedFallback ? "Budapest (default)" : "Your location"}
            </Tooltip>
          </CircleMarker>

          {/* Nearby stops, color-coded by mode (R2.1, R2.2). */}
          {(stops ?? []).map((stop) => (
            <CircleMarker
              key={stop.stop_id}
              center={[stop.lat, stop.lon]}
              radius={5}
              pathOptions={{
                color: "#334155",
                weight: 1,
                fillColor: colorForStop(stop, stopModes),
                fillOpacity: 0.9,
              }}
            >
              <Popup>{stop.name}</Popup>
            </CircleMarker>
          ))}

          {/* Highlighted line's route polyline (R2.4). */}
          {highlightColor && highlightShape && highlightShape.length > 0 && (
            <Polyline
              positions={highlightShape}
              pathOptions={{
                color: highlightColor,
                weight: 5,
                opacity: 0.85,
              }}
            />
          )}

          {/* Highlighted line's stops, emphasized in the line color (R2.4). */}
          {highlightColor &&
            (highlightStops ?? []).map((stop) => (
              <CircleMarker
                key={`highlight-${stop.stop_id}`}
                center={[stop.lat, stop.lon]}
                radius={7}
                pathOptions={{
                  color: "#FFFFFF",
                  weight: 2,
                  fillColor: highlightColor,
                  fillOpacity: 1,
                }}
              >
                <Popup>{stop.name}</Popup>
              </CircleMarker>
            ))}
        </MapContainer>
      </div>

      {usedFallback && (
        <p className="text-xs text-slate-500">
          Location unavailable — showing the default Budapest view.
        </p>
      )}
      {isError && (
        <p className="text-xs text-amber-600">
          Nearby stops could not be loaded.
        </p>
      )}
    </div>
  );
}
