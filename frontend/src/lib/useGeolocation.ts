/**
 * Geolocation hook for the transit map.
 *
 * Attempts to resolve the browser's current position via the Geolocation API
 * and falls back to the Budapest default coordinates whenever geolocation is
 * unavailable, denied, or errors out (Requirement 2.5). The hook never rejects:
 * it always resolves to a usable `{ lat, lon }` so the map can always render.
 */
import { useEffect, useState } from "react";

import { BUDAPEST_DEFAULT } from "./config";

export interface Coordinates {
  lat: number;
  lon: number;
}

export interface GeolocationState {
  /** The coordinates to use — the user's fix, or the Budapest fallback. */
  coords: Coordinates;
  /** True while the browser is still resolving the position. */
  loading: boolean;
  /** True when `coords` is the Budapest fallback rather than a real fix. */
  usedFallback: boolean;
}

export interface UseGeolocationOptions {
  /** Coordinates to fall back to. Defaults to the Budapest city center. */
  fallback?: Coordinates;
  /** Geolocation API options forwarded to `getCurrentPosition`. */
  positionOptions?: PositionOptions;
}

/**
 * Resolve the user's location, falling back to a default when unavailable.
 *
 * The hook starts on the fallback coordinates (so there is always something to
 * render) and, if a real fix arrives, swaps to it and clears `usedFallback`.
 */
export function useGeolocation(
  options: UseGeolocationOptions = {},
): GeolocationState {
  const fallback = options.fallback ?? BUDAPEST_DEFAULT;

  const [state, setState] = useState<GeolocationState>(() => ({
    coords: fallback,
    // Only "loading" if the API is present; otherwise we settle immediately.
    loading: typeof navigator !== "undefined" && "geolocation" in navigator,
    usedFallback: true,
  }));

  useEffect(() => {
    if (typeof navigator === "undefined" || !("geolocation" in navigator)) {
      // No Geolocation API: keep the fallback (Requirement 2.5).
      setState({ coords: fallback, loading: false, usedFallback: true });
      return;
    }

    let cancelled = false;

    navigator.geolocation.getCurrentPosition(
      (position) => {
        if (cancelled) return;
        setState({
          coords: {
            lat: position.coords.latitude,
            lon: position.coords.longitude,
          },
          loading: false,
          usedFallback: false,
        });
      },
      () => {
        // Permission denied / position unavailable / timeout: fall back.
        if (cancelled) return;
        setState({ coords: fallback, loading: false, usedFallback: true });
      },
      options.positionOptions,
    );

    return () => {
      cancelled = true;
    };
    // fallback is destructured to primitives so the effect is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fallback.lat, fallback.lon]);

  return state;
}
