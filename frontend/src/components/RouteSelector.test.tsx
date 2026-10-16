/**
 * Component tests for RouteSelector (Requirement 4).
 *
 * Focus:
 *   - disabled-until-line: the stop bar is disabled/empty and no stops request
 *     is made until a line is chosen (4.12).
 *   - scoped-to-direction: once a line is selected the stop options come only
 *     from the active direction's /stops response (4.3/4.9).
 *   - selecting a line resets the stop (4.3/4.12).
 *   - direction flip: swaps termini + stop order (4.4), preserves the selected
 *     stop when it exists in the opposite direction (4.7) and clears it when it
 *     does not (4.8); a single-direction line disables the flip control (4.10).
 */
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import RouteSelector from "./RouteSelector";
import type { Line, LineDirections, Stop } from "../lib/types";

// --- Mock the API client so no real network calls happen. ------------------
const linesMock = vi.fn();
const stopsForLineMock = vi.fn();

vi.mock("../lib/api", () => ({
  api: {
    lines: () => linesMock(),
    stopsForLine: (routeId: string, direction?: string) =>
      stopsForLineMock(routeId, direction),
  },
}));

const LINE_A: Line = {
  route_id: "R-A",
  short_name: "4",
  mode: "tram",
  color: "#FFD800",
};
const LINE_B: Line = {
  route_id: "R-B",
  short_name: "7",
  mode: "bus",
  color: "#4FC3F7",
};

// Line A, direction 0: Széll Kálmán -> Móricz -> Boráros.
const STOPS_A0: Stop[] = [
  { stop_id: "A1", name: "Széll Kálmán tér", lat: 47.5, lon: 19.02 },
  { stop_id: "A2", name: "Móricz Zsigmond körtér", lat: 47.47, lon: 19.04 },
  { stop_id: "A3", name: "Boráros tér", lat: 47.47, lon: 19.06 },
];
// Line A, direction 1: Boráros -> Móricz -> Széll Kálmán (reverse; same ids).
const STOPS_A1: Stop[] = [STOPS_A0[2], STOPS_A0[1], STOPS_A0[0]];
// Line B: single direction, disjoint stop ids.
const STOPS_B: Stop[] = [
  { stop_id: "B1", name: "Keleti pályaudvar", lat: 47.5, lon: 19.08 },
];

function lineDirectionsA(direction: string): LineDirections {
  const stops = direction === "1" ? STOPS_A1 : STOPS_A0;
  return {
    route_id: LINE_A.route_id,
    active_direction: direction === "1" ? "1" : "0",
    directions: [
      {
        direction_id: "0",
        start_terminus: "Széll Kálmán tér",
        end_terminus: "Boráros tér",
      },
      {
        direction_id: "1",
        start_terminus: "Boráros tér",
        end_terminus: "Széll Kálmán tér",
      },
    ],
    stops,
  };
}

const LINE_DIRECTIONS_B: LineDirections = {
  route_id: LINE_B.route_id,
  active_direction: "0",
  directions: [
    { direction_id: "0", start_terminus: "Keleti pályaudvar", end_terminus: "Keleti pályaudvar" },
  ],
  stops: STOPS_B,
};

const status = { source: "mock", fetched_at: "2024-01-01T00:00:00Z", stale: false };

function Harness() {
  const [line, setLine] = useState<Line | null>(null);
  const [stop, setStop] = useState<Stop | null>(null);
  return (
    <RouteSelector
      selectedLine={line}
      selectedStop={stop}
      onLineChange={setLine}
      onStopChange={setStop}
    />
  );
}

function renderSelector() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  linesMock.mockResolvedValue({ data: [LINE_A, LINE_B], status });
  stopsForLineMock.mockImplementation((routeId: string, direction?: string) =>
    Promise.resolve({
      data:
        routeId === LINE_A.route_id
          ? lineDirectionsA(direction ?? "0")
          : LINE_DIRECTIONS_B,
      status,
    }),
  );
});

afterEach(() => {
  vi.clearAllMocks();
});

async function selectLineA(user: ReturnType<typeof userEvent.setup>) {
  const lineOptions = await screen.findByRole("listbox", {
    name: "Available lines",
  });
  await user.click(within(lineOptions).getByRole("button", { name: /^4 / }));
}

describe("RouteSelector", () => {
  it("keeps the stop search disabled and fetches no stops until a line is chosen", async () => {
    renderSelector();

    await screen.findByRole("option", { name: /4/ });

    const stopInput = screen.getByLabelText("Stop") as HTMLInputElement;
    expect(stopInput).toBeDisabled();
    expect(stopInput.value).toBe("");
    expect(
      screen.queryByRole("listbox", { name: "Line stops" }),
    ).not.toBeInTheDocument();
    expect(stopsForLineMock).not.toHaveBeenCalled();
  });

  it("enables the stop search scoped to the selected line's active direction", async () => {
    const user = userEvent.setup();
    renderSelector();
    await selectLineA(user);

    await waitFor(() =>
      expect(stopsForLineMock).toHaveBeenCalledWith(LINE_A.route_id, undefined),
    );

    expect(screen.getByLabelText("Stop")).toBeEnabled();

    const stopList = await screen.findByRole("listbox", { name: "Line stops" });
    expect(
      within(stopList).getByRole("option", { name: /Széll Kálmán tér/ }),
    ).toBeInTheDocument();
    expect(
      within(stopList).getByRole("option", { name: /Boráros tér/ }),
    ).toBeInTheDocument();
    expect(
      within(stopList).queryByRole("option", { name: /Keleti/ }),
    ).not.toBeInTheDocument();
  });

  it("shows the active direction's termini and flips them on the flip control", async () => {
    const user = userEvent.setup();
    renderSelector();
    await selectLineA(user);

    // Direction 0 termini.
    await waitFor(() =>
      expect(screen.getByTestId("direction-start")).toHaveTextContent(
        "Széll Kálmán tér",
      ),
    );
    expect(screen.getByTestId("direction-end")).toHaveTextContent("Boráros tér");

    // Flip -> direction 1 requested, termini swap.
    await user.click(
      screen.getByRole("button", { name: /flip travel direction/i }),
    );
    await waitFor(() =>
      expect(stopsForLineMock).toHaveBeenCalledWith(LINE_A.route_id, "1"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("direction-start")).toHaveTextContent(
        "Boráros tér",
      ),
    );
    expect(screen.getByTestId("direction-end")).toHaveTextContent(
      "Széll Kálmán tér",
    );
  });

  it("preserves the selected stop across a flip when it exists in the opposite direction", async () => {
    const user = userEvent.setup();
    renderSelector();
    await selectLineA(user);

    const stopList = await screen.findByRole("listbox", { name: "Line stops" });
    await user.click(
      within(stopList).getByRole("button", { name: /Móricz/ }),
    );
    expect(screen.getByTestId("selected-stop")).toHaveTextContent("Móricz");

    // Móricz is on both directions -> preserved after the flip.
    await user.click(
      screen.getByRole("button", { name: /flip travel direction/i }),
    );
    await waitFor(() =>
      expect(stopsForLineMock).toHaveBeenCalledWith(LINE_A.route_id, "1"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("direction-start")).toHaveTextContent(
        "Boráros tér",
      ),
    );
    expect(screen.getByTestId("selected-stop")).toHaveTextContent("Móricz");
  });

  it("renders the flip control disabled for a single-direction line", async () => {
    const user = userEvent.setup();
    renderSelector();

    const lineOptions = await screen.findByRole("listbox", {
      name: "Available lines",
    });
    await user.click(within(lineOptions).getByRole("button", { name: /^7 / }));

    const flip = await screen.findByRole("button", {
      name: /flip travel direction/i,
    });
    expect(flip).toBeDisabled();
  });

  it("filters the stop options as the user searches", async () => {
    const user = userEvent.setup();
    renderSelector();
    await selectLineA(user);

    await screen.findByRole("listbox", { name: "Line stops" });
    const stopInput = await screen.findByLabelText("Stop");
    await user.type(stopInput, "Boráros");

    const stopList = screen.getByRole("listbox", { name: "Line stops" });
    expect(
      within(stopList).getByRole("option", { name: /Boráros tér/ }),
    ).toBeInTheDocument();
    expect(
      within(stopList).queryByRole("option", { name: /Széll Kálmán/ }),
    ).not.toBeInTheDocument();
  });

  it("resets the selected stop when a different line is chosen", async () => {
    const user = userEvent.setup();
    renderSelector();

    const lineOptions = await screen.findByRole("listbox", {
      name: "Available lines",
    });
    await user.click(within(lineOptions).getByRole("button", { name: /^4 / }));
    const stopList = await screen.findByRole("listbox", { name: "Line stops" });
    await user.click(
      within(stopList).getByRole("button", { name: /Széll Kálmán tér/ }),
    );
    expect(screen.getByTestId("selected-stop")).toHaveTextContent(
      "Széll Kálmán tér",
    );

    await user.click(within(lineOptions).getByRole("button", { name: /^7 / }));

    await waitFor(() =>
      expect(screen.queryByTestId("selected-stop")).not.toBeInTheDocument(),
    );
    const newStopList = await screen.findByRole("listbox", {
      name: "Line stops",
    });
    expect(
      within(newStopList).getByRole("option", { name: /Keleti/ }),
    ).toBeInTheDocument();
  });
});
