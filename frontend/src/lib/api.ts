/**
 * API client for the Budapest Transit Monitor backend.
 *
 * Responsibilities:
 *  - Point at the backend base URL (configurable via `VITE_API_BASE_URL`;
 *    defaults to `/api`, which the Vite dev server proxies to the backend).
 *  - Unwrap the shared response envelope
 *      `{ data, data_status: { source, fetched_at, stale } }`
 *    so callers receive `{ data, status }` with the two concerns separated.
 *  - Surface non-2xx responses and network errors as `ApiError` so the UI can
 *    render a degraded/error state (Requirement 10.3).
 */
import type {
  DataStatus,
  Departure,
  Envelope,
  Line,
  LineDirections,
  Prediction,
  ProblemScore,
  RouteDelay,
  Stop,
  TimelineNode,
  Weather,
} from "./types";

/**
 * Base URL for API calls. In development this stays `/api` and the Vite proxy
 * forwards it to the FastAPI backend (default http://localhost:8000). In other
 * environments set `VITE_API_BASE_URL` (e.g. an absolute backend origin).
 */
export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(
    /\/$/,
    "",
  ) ?? "/api";

/** Error thrown for any failed request (network error or non-2xx status). */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** A response with the envelope unwrapped into separate data + status parts. */
export interface ApiResult<T> {
  data: T;
  status: DataStatus;
}

function buildUrl(path: string, params?: Record<string, unknown>): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  const query = new URLSearchParams();
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null) {
        query.append(key, String(value));
      }
    }
  }
  const qs = query.toString();
  return `${API_BASE_URL}${normalizedPath}${qs ? `?${qs}` : ""}`;
}

/**
 * Perform a GET request and unwrap the data_status envelope.
 *
 * @throws {ApiError} on network failure, non-2xx status, or a malformed body.
 */
export async function getEnvelope<T>(
  path: string,
  params?: Record<string, unknown>,
  init?: RequestInit,
): Promise<ApiResult<T>> {
  const url = buildUrl(path, params);

  let response: Response;
  try {
    response = await fetch(url, {
      headers: { Accept: "application/json" },
      ...init,
    });
  } catch (cause) {
    // Network-level failure (backend unreachable, DNS, CORS, etc.).
    throw new ApiError(
      `Network error requesting ${url}: ${(cause as Error).message}`,
      0,
    );
  }

  if (!response.ok) {
    throw new ApiError(
      `Request to ${url} failed with status ${response.status}`,
      response.status,
    );
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ApiError(`Malformed JSON from ${url}`, response.status);
  }

  const envelope = body as Envelope<T>;
  if (
    envelope == null ||
    typeof envelope !== "object" ||
    !("data" in envelope) ||
    !("data_status" in envelope)
  ) {
    throw new ApiError(`Unexpected response shape from ${url}`, response.status);
  }

  return { data: envelope.data, status: envelope.data_status };
}

/**
 * Typed endpoint helpers. Each returns the unwrapped `{ data, status }` pair.
 * These mirror the endpoints documented in design.md.
 */
export const api = {
  status: () => getEnvelope<DataStatus>("/status"),

  weather: (lat: number, lon: number) =>
    getEnvelope<Weather | null>("/weather", { lat, lon }),

  lines: () => getEnvelope<Line[]>("/lines"),

  stopsForLine: (routeId: string, direction?: string) =>
    getEnvelope<LineDirections | null>(
      `/lines/${encodeURIComponent(routeId)}/stops`,
      direction != null ? { direction } : undefined,
    ),

  lineShape: (routeId: string) =>
    getEnvelope<[number, number][]>(
      `/lines/${encodeURIComponent(routeId)}/shape`,
    ),

  stopsNear: (lat: number, lon: number, radius?: number) =>
    getEnvelope<Stop[]>("/stops/near", { lat, lon, radius }),

  delays: () => getEnvelope<RouteDelay[]>("/delays"),

  problematicRoutes: (limit = 5) =>
    getEnvelope<ProblemScore[]>("/problematic-routes", { limit }),

  departures: (routeId: string, stopId: string) =>
    getEnvelope<Departure[]>("/departures", {
      route_id: routeId,
      stop_id: stopId,
    }),

  predict: (routeId: string, stopId: string) =>
    getEnvelope<Prediction>("/predict", {
      route_id: routeId,
      stop_id: stopId,
    }),

  timeline: (routeId: string, stopId: string, tripId?: string) =>
    getEnvelope<TimelineNode[]>("/timeline", {
      route_id: routeId,
      stop_id: stopId,
      // When a departure is selected the timeline is anchored on that exact
      // trip so every node reflects that run's times (not just the head).
      trip_id: tripId,
    }),
};
