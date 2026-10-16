/**
 * JSON shapes returned by the backend REST API.
 *
 * These mirror the Pydantic models in `backend/models.py`. They intentionally
 * describe the JSON representation (e.g. datetimes arrive as ISO-8601 strings).
 */
import type { Mode } from "./colors";

export type { Mode };

/** The shared data-status object attached to every API response. */
export interface DataStatus {
  source: "realtime" | "mock";
  /** ISO-8601 timestamp of when the underlying data was fetched. */
  fetched_at: string;
  stale: boolean;
}

/** The shared response envelope wrapping every endpoint's payload. */
export interface Envelope<T> {
  data: T;
  data_status: DataStatus;
}

export interface Line {
  route_id: string;
  short_name: string;
  mode: Mode;
  color: string;
}

export interface Stop {
  stop_id: string;
  name: string;
  lat: number;
  lon: number;
}

/** One travel orientation of a line (GTFS direction_id). R4. */
export interface Direction {
  direction_id: string;
  start_terminus: string | null;
  end_terminus: string | null;
}

/** A line's available directions plus the active direction's ordered stops. R4. */
export interface LineDirections {
  route_id: string;
  active_direction: string;
  directions: Direction[];
  stops: Stop[];
}

export interface RouteDelay {
  route_id: string;
  short_name: string;
  mode: Mode;
  avg_delay_min: number;
  max_delay_min: number;
}

export interface ProblemScore {
  route_id: string;
  short_name: string;
  mode: Mode;
  score: number;
  avg_delay_min: number;
}

export interface Departure {
  trip_id: string;
  scheduled: string;
  delay_min: number;
  expected: string;
}

export interface Weather {
  temperature_c: number;
  condition: string;
  weather_code: number;
  wind_kph: number;
  precipitation_mm: number;
}

export interface Prediction {
  expected_travel_min: number;
  scheduled_travel_min: number;
  adjustment_min: number;
  weather_used: boolean;
  available: boolean;
}

export interface TimelineNode {
  stop_id: string;
  name: string;
  stop_sequence: number;
  expected_arrival: string;
  is_current: boolean;
}
