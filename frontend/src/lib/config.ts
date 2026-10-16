/**
 * Frontend configuration constants.
 *
 * These mirror the backend defaults in `backend/config.py`. In particular the
 * Budapest default coordinates are the fallback the map centers on when the
 * browser cannot provide a geolocation fix (Requirement 2.5).
 */

/** Budapest city-center coordinates (matches backend CITY_LAT / CITY_LON). */
export const BUDAPEST_DEFAULT: { lat: number; lon: number } = {
  lat: 47.4979,
  lon: 19.0402,
};

/** Default radius (metres) used when querying nearby stops for the map. */
export const NEARBY_STOPS_RADIUS_M = 800;

/** Default zoom level for the transit map. */
export const DEFAULT_MAP_ZOOM = 14;
