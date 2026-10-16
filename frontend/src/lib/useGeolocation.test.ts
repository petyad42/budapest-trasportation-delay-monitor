import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BUDAPEST_DEFAULT } from "./config";
import { useGeolocation } from "./useGeolocation";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useGeolocation", () => {
  it("falls back to Budapest when the Geolocation API is absent", async () => {
    // No `geolocation` on navigator.
    vi.stubGlobal("navigator", {} as Navigator);

    const { result } = renderHook(() => useGeolocation());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.usedFallback).toBe(true);
    expect(result.current.coords).toEqual(BUDAPEST_DEFAULT);
  });

  it("falls back to Budapest when the user denies permission", async () => {
    const getCurrentPosition = vi.fn(
      (_success: PositionCallback, error?: PositionErrorCallback) => {
        error?.({ code: 1, message: "denied" } as GeolocationPositionError);
      },
    );
    vi.stubGlobal("navigator", {
      geolocation: { getCurrentPosition },
    } as unknown as Navigator);

    const { result } = renderHook(() => useGeolocation());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.usedFallback).toBe(true);
    expect(result.current.coords).toEqual(BUDAPEST_DEFAULT);
  });

  it("uses the resolved position when geolocation succeeds", async () => {
    const getCurrentPosition = vi.fn((success: PositionCallback) => {
      success({
        coords: { latitude: 47.51, longitude: 19.06 },
      } as GeolocationPosition);
    });
    vi.stubGlobal("navigator", {
      geolocation: { getCurrentPosition },
    } as unknown as Navigator);

    const { result } = renderHook(() => useGeolocation());

    await act(async () => {
      await waitFor(() => expect(result.current.loading).toBe(false));
    });
    expect(result.current.usedFallback).toBe(false);
    expect(result.current.coords).toEqual({ lat: 47.51, lon: 19.06 });
  });
});
