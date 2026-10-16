import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Dashboard from "./Dashboard";
import { formatArrival } from "./ArrivalTimeline";
import type { ApiResult } from "../lib/api";
import type {
  DataStatus,
  Departure,
  Line,
  ProblemScore,
  Stop,
  TimelineNode,
  Weather,
} from "../lib/types";

// react-leaflet needs a real DOM map; stub it so TransitMap renders in jsdom.
vi.mock("react-leaflet", () => {
  const Passthrough = ({ children }: { children?: ReactNode }) => (
    <div>{children}</div>
  );
  return {
    MapContainer: Passthrough,
    TileLayer: () => <div data-testid="tile-layer" />,
    CircleMarker: Passthrough,
    Polyline: () => <div data-testid="polyline" />,
    Popup: Passthrough,
    Tooltip: Passthrough,
  };
});

// --- Mock the API client -----------------------------------------------------
const statusFn = vi.fn();
const weatherFn = vi.fn();
const linesFn = vi.fn();
const stopsForLineFn = vi.fn();
const lineShapeFn = vi.fn();
const stopsNearFn = vi.fn();
const problematicRoutesFn = vi.fn();
const departuresFn = vi.fn();
const predictFn = vi.fn();
const timelineFn = vi.fn();

vi.mock("../lib/api", () => ({
  api: {
    status: (...a: unknown[]) => statusFn(...a),
    weather: (...a: unknown[]) => weatherFn(...a),
    lines: (...a: unknown[]) => linesFn(...a),
    stopsForLine: (...a: unknown[]) => stopsForLineFn(...a),
    lineShape: (...a: unknown[]) => lineShapeFn(...a),
    stopsNear: (...a: unknown[]) => stopsNearFn(...a),
    problematicRoutes: (...a: unknown[]) => problematicRoutesFn(...a),
    departures: (...a: unknown[]) => departuresFn(...a),
    predict: (...a: unknown[]) => predictFn(...a),
    timeline: (...a: unknown[]) => timelineFn(...a),
  },
}));

const OK_STATUS: DataStatus = {
  source: "realtime",
  fetched_at: "2024-01-01T00:00:00Z",
  stale: false,
};

function result<T>(data: T): ApiResult<T> {
  return { data, status: OK_STATUS };
}

const WEATHER: Weather = {
  temperature_c: 12,
  condition: "Partly cloudy",
  weather_code: 2,
  wind_kph: 8,
  precipitation_mm: 0,
};

const LINES: Line[] = [
  { route_id: "T4", short_name: "4", mode: "tram", color: "" },
];

const TOP5: ProblemScore[] = [
  { route_id: "B7", short_name: "7", mode: "bus", score: 0.9, avg_delay_min: 5 },
];

const STOPS: Stop[] = [];

function renderDashboard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      {/* Disable polling in tests. */}
      <Dashboard statusPollMs={0} weatherPollMs={0} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.stubGlobal("navigator", {} as Navigator);
  // The real /api/status envelope: `data` is the per-source breakdown (which
  // has NO top-level `source`), while the badge must read the envelope's
  // `status`. Returning distinct shapes here catches a regression where the
  // Dashboard reads `.data` instead of `.status` for the badge.
  statusFn.mockResolvedValue({
    data: { realtime: OK_STATUS, catalog: OK_STATUS },
    status: OK_STATUS,
  });
  weatherFn.mockResolvedValue(result(WEATHER));
  linesFn.mockResolvedValue(result(LINES));
  stopsForLineFn.mockResolvedValue(
    result({
      route_id: "T4",
      active_direction: "0",
      directions: [
        { direction_id: "0", start_terminus: null, end_terminus: null },
      ],
      stops: STOPS,
    }),
  );
  lineShapeFn.mockResolvedValue(result([]));
  stopsNearFn.mockResolvedValue(result(STOPS));
  problematicRoutesFn.mockResolvedValue(result(TOP5));
  departuresFn.mockResolvedValue(result([]));
  predictFn.mockResolvedValue(
    result({
      expected_travel_min: 10,
      scheduled_travel_min: 10,
      adjustment_min: 0,
      weather_used: true,
      available: true,
    }),
  );
  timelineFn.mockResolvedValue(result([]));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Dashboard layout", () => {
  it("renders the header, left column, and right column per the wireframe", async () => {
    renderDashboard();

    // Header title.
    expect(
      screen.getByRole("heading", { name: /budapest public transport/i }),
    ).toBeInTheDocument();

    // Two labelled sections: left "Local lines info", right "Choose a route".
    expect(
      screen.getByRole("region", { name: /local lines info/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: /choose a route/i }),
    ).toBeInTheDocument();

    // Left column contains the Top 5 list; right column the route selector.
    expect(
      screen.getByRole("region", { name: /top 5 problematic routes/i }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/^line$/i)).toBeInTheDocument();

    // Right column panels + timeline.
    expect(
      screen.getByRole("region", { name: /upcoming departures/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: /^prediction$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: /arrival timeline/i }),
    ).toBeInTheDocument();
  });

  it("shows the real-time badge and weather from the polled queries", async () => {
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByText(/real-time data/i)).toBeInTheDocument(),
    );
    expect(screen.getByText("12°C")).toBeInTheDocument();
    expect(screen.getByText(/partly cloudy/i)).toBeInTheDocument();
  });

  it("renders a degraded banner and neutral header when the backend is unreachable (R10.3)", async () => {
    statusFn.mockRejectedValue(new Error("network down"));
    weatherFn.mockRejectedValue(new Error("network down"));

    renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        /backend is currently unreachable/i,
      ),
    );
    // Badge falls back to its neutral "data unavailable" state, not blank.
    expect(screen.getByText(/data unavailable/i)).toBeInTheDocument();
    expect(screen.getByText(/weather unavailable/i)).toBeInTheDocument();
  });
});

describe("Dashboard departure selection drives the arrival timeline", () => {
  const STOP: Stop = { stop_id: "S1", name: "Deák Ferenc tér", lat: 0, lon: 0 };

  const DEPARTURES: Departure[] = [
    {
      trip_id: "trip-1",
      scheduled: "2024-01-01T08:05:00Z",
      delay_min: 4,
      expected: "2024-01-01T08:09:00Z",
    },
    {
      trip_id: "trip-2",
      scheduled: "2024-01-01T08:20:00Z",
      delay_min: 0,
      expected: "2024-01-01T08:20:00Z",
    },
  ];

  const TIMELINE: TimelineNode[] = [
    {
      stop_id: "S1",
      name: "Deák Ferenc tér",
      stop_sequence: 5,
      expected_arrival: "2024-01-01T08:00:00Z",
      is_current: true,
    },
  ];

  beforeEach(() => {
    // A line with a single direction and one stop so the selectors can resolve.
    stopsForLineFn.mockResolvedValue(
      result({
        route_id: "T4",
        active_direction: "0",
        directions: [
          { direction_id: "0", start_terminus: "A", end_terminus: "B" },
        ],
        stops: [STOP],
      }),
    );
    departuresFn.mockResolvedValue(result(DEPARTURES));
    // Trip-anchored timeline: when a trip_id is passed (a departure was
    // selected) the head node carries that trip's own arrival; otherwise the
    // default next-trip node (08:00).
    timelineFn.mockImplementation((...args: unknown[]) => {
      const tripId = args[2] as string | undefined;
      if (tripId === "trip-1") {
        return Promise.resolve(
          result([{ ...TIMELINE[0], expected_arrival: DEPARTURES[0].expected }]),
        );
      }
      return Promise.resolve(result(TIMELINE));
    });
  });

  async function selectLineAndStop(user: ReturnType<typeof userEvent.setup>) {
    // Pick the line from the route selector's line listbox (the "Available
    // lines" listbox — LocalLinesList also renders a lines listbox).
    const lineList = await screen.findByRole("listbox", {
      name: /available lines/i,
    });
    const lineOption = within(lineList).getByRole("option", { name: /tram/i });
    await user.click(within(lineOption).getByRole("button"));
    // Pick the one stop from the stop listbox.
    const stopList = await screen.findByRole("listbox", { name: /line stops/i });
    const stopOption = within(stopList).getByRole("option", {
      name: /deák ferenc tér/i,
    });
    await user.click(within(stopOption).getByRole("button"));
  }

  it("colors the timeline head with the clicked departure's delay", async () => {
    const user = userEvent.setup();
    renderDashboard();

    await selectLineAndStop(user);

    // The departures list (a listbox) renders the two departures.
    const departuresList = await screen.findByRole("listbox", {
      name: /upcoming departures/i,
    });
    const options = within(departuresList).getAllByRole("option");
    await user.click(within(options[0]).getByRole("button"));

    // Timeline refetches anchored on trip-1; the head shows that trip's
    // 08:09 arrival, colored late/red by the selected departure's delay.
    const timeline = screen.getByRole("region", { name: /arrival timeline/i });
    const head = within(timeline).getAllByRole("listitem")[0];
    const time = within(head).getByText(formatArrival(DEPARTURES[0].expected));
    expect(time).toHaveClass("text-red-600");
  });

  it("clears the departure selection when the stop changes", async () => {
    const user = userEvent.setup();
    renderDashboard();

    await selectLineAndStop(user);

    const departuresList = await screen.findByRole("listbox", {
      name: /upcoming departures/i,
    });
    const options = within(departuresList).getAllByRole("option");
    await user.click(within(options[0]).getByRole("button"));

    // Re-selecting the stop clears the departure; the timeline reverts to the
    // node's own 08:00 time in the default color.
    const stopList = screen.getByRole("listbox", { name: /line stops/i });
    const stopOption = within(stopList).getByRole("option", {
      name: /deák ferenc tér/i,
    });
    await user.click(within(stopOption).getByRole("button"));

    const timeline = screen.getByRole("region", { name: /arrival timeline/i });
    const head = within(timeline).getAllByRole("listitem")[0];
    const time = within(head).getByText(
      formatArrival(TIMELINE[0].expected_arrival),
    );
    expect(time).toHaveClass("text-slate-600");
  });
});
