import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import TransitMap from "./TransitMap";
import { BUS_COLOR, TRAM_COLOR } from "../lib/colors";
import type { Line, Stop } from "../lib/types";

// --- Mock react-leaflet ------------------------------------------------------
// react-leaflet needs a real DOM map; in jsdom we replace it with lightweight
// stand-ins that expose the props we care about (center, tile url, fill color).
vi.mock("react-leaflet", () => {
  return {
    MapContainer: ({
      center,
      children,
      ...rest
    }: {
      center: [number, number];
      children?: ReactNode;
    } & Record<string, unknown>) => (
      <div
        data-testid="map"
        data-center={JSON.stringify(center)}
        aria-label={rest["aria-label"] as string}
      >
        {children}
      </div>
    ),
    TileLayer: ({ url }: { url: string }) => (
      <div data-testid="tile-layer" data-url={url} />
    ),
    CircleMarker: ({
      pathOptions,
      children,
    }: {
      pathOptions?: { fillColor?: string };
      children?: ReactNode;
    }) => (
      <div data-testid="circle-marker" data-fill={pathOptions?.fillColor}>
        {children}
      </div>
    ),
    Polyline: ({
      positions,
      pathOptions,
    }: {
      positions: [number, number][];
      pathOptions?: { color?: string };
    }) => (
      <div
        data-testid="polyline"
        data-color={pathOptions?.color}
        data-points={JSON.stringify(positions)}
      />
    ),
    Popup: ({ children }: { children?: ReactNode }) => (
      <div data-testid="popup">{children}</div>
    ),
    Tooltip: ({ children }: { children?: ReactNode }) => (
      <div data-testid="tooltip">{children}</div>
    ),
  };
});

// --- Mock the API client -----------------------------------------------------
const stopsNear = vi.fn();
const lineShape = vi.fn();
const stopsForLine = vi.fn();
vi.mock("../lib/api", () => ({
  api: {
    stopsNear: (...args: unknown[]) => stopsNear(...args),
    lineShape: (...args: unknown[]) => lineShape(...args),
    stopsForLine: (...args: unknown[]) => stopsForLine(...args),
  },
}));

function renderWithClient(ui: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

const SAMPLE_STOPS: Stop[] = [
  { stop_id: "S1", name: "Deák Ferenc tér", lat: 47.4979, lon: 19.0402 },
  { stop_id: "S2", name: "Astoria", lat: 47.4938, lon: 19.0596 },
  { stop_id: "S3", name: "Unknown mode stop", lat: 47.5, lon: 19.05 },
];

beforeEach(() => {
  // No geolocation -> component uses the Budapest fallback center.
  vi.stubGlobal("navigator", {} as Navigator);
  stopsNear.mockResolvedValue({
    data: SAMPLE_STOPS,
    status: { source: "mock", fetched_at: "2024-01-01T00:00:00Z", stale: false },
  });
  lineShape.mockResolvedValue({
    data: [
      [47.4979, 19.0402],
      [47.4938, 19.0596],
    ],
    status: { source: "mock", fetched_at: "2024-01-01T00:00:00Z", stale: false },
  });
  stopsForLine.mockResolvedValue({
    data: {
      route_id: "T4",
      active_direction: "0",
      directions: [
        { direction_id: "0", start_terminus: null, end_terminus: null },
      ],
      stops: [SAMPLE_STOPS[0], SAMPLE_STOPS[1]],
    },
    status: { source: "mock", fetched_at: "2024-01-01T00:00:00Z", stale: false },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("TransitMap", () => {
  it("renders an OSM tile layer centered on the Budapest fallback", async () => {
    renderWithClient(<TransitMap />);

    const map = await screen.findByTestId("map");
    expect(JSON.parse(map.getAttribute("data-center")!)).toEqual([
      47.4979, 19.0402,
    ]);

    const tile = screen.getByTestId("tile-layer");
    expect(tile.getAttribute("data-url")).toContain(
      "tile.openstreetmap.org",
    );
  });

  it("shows the default-view notice when geolocation is unavailable", async () => {
    renderWithClient(<TransitMap />);
    expect(
      await screen.findByText(/default Budapest view/i),
    ).toBeInTheDocument();
  });

  it("plots nearby stops color-coded by mode", async () => {
    renderWithClient(
      <TransitMap
        stopModes={{
          S1: { mode: "tram" },
          S2: { mode: "bus" },
          // S3 intentionally omitted -> neutral color.
        }}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText("Deák Ferenc tér")).toBeInTheDocument(),
    );

    const markers = screen.getAllByTestId("circle-marker");
    // 1 user-location marker + 3 stop markers.
    const fills = markers.map((m) => m.getAttribute("data-fill"));
    expect(fills).toContain(TRAM_COLOR); // S1
    expect(fills).toContain(BUS_COLOR); // S2
    // The unknown-mode stop uses the neutral color, not a mode color.
    expect(fills).toContain("#94A3B8");
  });

  it("requests nearby stops for the resolved center", async () => {
    renderWithClient(<TransitMap radiusM={800} />);
    await waitFor(() =>
      expect(stopsNear).toHaveBeenCalledWith(47.4979, 19.0402, 800),
    );
  });

  it("does not fetch a route shape when no line is highlighted", async () => {
    renderWithClient(<TransitMap />);
    await screen.findByTestId("map");
    expect(lineShape).not.toHaveBeenCalled();
    expect(screen.queryByTestId("polyline")).not.toBeInTheDocument();
  });

  it("draws the highlighted line's route and stops in its mode color (R2.4)", async () => {
    const line: Line = {
      route_id: "T4",
      short_name: "4",
      mode: "tram",
      color: "",
    };
    renderWithClient(<TransitMap highlightedLine={line} />);

    const polyline = await screen.findByTestId("polyline");
    expect(polyline.getAttribute("data-color")).toBe(TRAM_COLOR);
    expect(JSON.parse(polyline.getAttribute("data-points")!)).toEqual([
      [47.4979, 19.0402],
      [47.4938, 19.0596],
    ]);

    // The line's stops are fetched and emphasized in the line color.
    await waitFor(() => expect(stopsForLine).toHaveBeenCalledWith("T4"));
    expect(lineShape).toHaveBeenCalledWith("T4");

    const fills = screen
      .getAllByTestId("circle-marker")
      .map((m) => m.getAttribute("data-fill"));
    // Emphasized highlight stops use the tram color.
    expect(fills.filter((f) => f === TRAM_COLOR).length).toBeGreaterThanOrEqual(
      2,
    );
  });
});
