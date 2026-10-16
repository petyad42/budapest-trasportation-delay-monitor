import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import DeparturesPanel, { formatDelay } from "./DeparturesPanel";
import { api } from "../lib/api";
import type { ApiResult } from "../lib/api";
import type { Departure } from "../lib/types";

const status = {
  source: "realtime" as const,
  fetched_at: "2024-01-01T00:00:00Z",
  stale: false,
};

function result(data: Departure[]): ApiResult<Departure[]> {
  return { data, status };
}

function renderWithClient(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>{node}</QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("formatDelay", () => {
  it("renders positive delays with a + sign", () => {
    expect(formatDelay(3)).toBe("+3 min");
    expect(formatDelay(2.25)).toBe("+2.3 min");
  });

  it("renders zero as 'on time' and negatives as-is", () => {
    expect(formatDelay(0)).toBe("on time");
    expect(formatDelay(-1)).toBe("-1 min");
  });
});

describe("DeparturesPanel", () => {
  it("shows a prompt state when no line/stop is selected", () => {
    const spy = vi.spyOn(api, "departures");

    renderWithClient(
      <DeparturesPanel
        routeId={null}
        stopId={null}
        selectedDeparture={null}
        onSelectDeparture={() => {}}
      />,
    );

    expect(screen.getByText(/select a line and stop/i)).toBeInTheDocument();
    expect(spy).not.toHaveBeenCalled();
  });

  it("lists upcoming departures with scheduled time and delay (R5.2)", async () => {
    const departures: Departure[] = [
      {
        trip_id: "trip-1",
        scheduled: "2024-01-01T08:05:00Z",
        delay_min: 3,
        expected: "2024-01-01T08:08:00Z",
      },
      {
        trip_id: "trip-2",
        scheduled: "2024-01-01T08:20:00Z",
        delay_min: 0,
        expected: "2024-01-01T08:20:00Z",
      },
    ];
    vi.spyOn(api, "departures").mockResolvedValue(result(departures));

    renderWithClient(
      <DeparturesPanel
        routeId="T4"
        stopId="S1"
        selectedDeparture={null}
        onSelectDeparture={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("+3 min")).toBeInTheDocument();
    });
    expect(screen.getByText("on time")).toBeInTheDocument();
  });

  it("shows an empty state when there are no departures", async () => {
    vi.spyOn(api, "departures").mockResolvedValue(result([]));

    renderWithClient(
      <DeparturesPanel
        routeId="T4"
        stopId="S1"
        selectedDeparture={null}
        onSelectDeparture={() => {}}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/no upcoming departures/i)).toBeInTheDocument();
    });
  });

  it("renders a degraded state when the backend is unreachable (R10.3)", async () => {
    vi.spyOn(api, "departures").mockRejectedValue(new Error("network down"));

    renderWithClient(
      <DeparturesPanel
        routeId="T4"
        stopId="S1"
        selectedDeparture={null}
        onSelectDeparture={() => {}}
      />,
    );

    await waitFor(() => {
      expect(
        screen.getByText(/departures are currently unavailable/i),
      ).toBeInTheDocument();
    });
  });

  it("notifies the parent when a departure is clicked, and clears on re-click", async () => {
    const user = userEvent.setup();
    const departures: Departure[] = [
      {
        trip_id: "trip-1",
        scheduled: "2024-01-01T08:05:00Z",
        delay_min: 3,
        expected: "2024-01-01T08:08:00Z",
      },
    ];
    vi.spyOn(api, "departures").mockResolvedValue(result(departures));
    const onSelect = vi.fn();

    const { rerender } = renderWithClient(
      <DeparturesPanel
        routeId="T4"
        stopId="S1"
        selectedDeparture={null}
        onSelectDeparture={onSelect}
      />,
    );

    const option = await screen.findByRole("option");
    await user.click(within(option).getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith(departures[0]);

    // When that departure is already selected, clicking it again clears it.
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <DeparturesPanel
          routeId="T4"
          stopId="S1"
          selectedDeparture={departures[0]}
          onSelectDeparture={onSelect}
        />
      </QueryClientProvider>,
    );
    const selected = await screen.findByRole("option");
    await user.click(within(selected).getByRole("button"));
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });

  it("marks the selected departure with aria-selected", async () => {
    const departures: Departure[] = [
      {
        trip_id: "trip-1",
        scheduled: "2024-01-01T08:05:00Z",
        delay_min: 3,
        expected: "2024-01-01T08:08:00Z",
      },
      {
        trip_id: "trip-2",
        scheduled: "2024-01-01T08:20:00Z",
        delay_min: 0,
        expected: "2024-01-01T08:20:00Z",
      },
    ];
    vi.spyOn(api, "departures").mockResolvedValue(result(departures));

    renderWithClient(
      <DeparturesPanel
        routeId="T4"
        stopId="S1"
        selectedDeparture={departures[1]}
        onSelectDeparture={() => {}}
      />,
    );

    await screen.findByText("on time");
    const options = screen.getAllByRole("option");
    expect(options[0]).toHaveAttribute("aria-selected", "false");
    expect(options[1]).toHaveAttribute("aria-selected", "true");
  });

  it("grays out past departures while keeping future ones normal", async () => {
    const now = new Date("2024-01-01T08:10:00Z").getTime();
    const departures: Departure[] = [
      {
        trip_id: "past",
        scheduled: "2024-01-01T08:00:00Z",
        delay_min: 2,
        expected: "2024-01-01T08:02:00Z", // before `now` -> past
      },
      {
        trip_id: "future",
        scheduled: "2024-01-01T08:20:00Z",
        delay_min: 2,
        expected: "2024-01-01T08:22:00Z", // after `now` -> future
      },
    ];
    vi.spyOn(api, "departures").mockResolvedValue(result(departures));

    renderWithClient(
      <DeparturesPanel
        routeId="T4"
        stopId="S1"
        selectedDeparture={null}
        onSelectDeparture={() => {}}
        now={now}
      />,
    );

    await screen.findAllByText("+2 min");
    const options = screen.getAllByRole("option");
    // Past row mutes its delay text; future row keeps the semantic late color.
    expect(within(options[0]).getByText("+2 min")).toHaveClass("text-slate-400");
    expect(within(options[1]).getByText("+2 min")).toHaveClass("text-red-600");
  });

  it("colors a rush (early) delay yellow and a late delay red", async () => {
    // Use a `now` before every expected time so neither row is muted as past.
    const now = new Date("2024-01-01T00:00:00Z").getTime();
    const departures: Departure[] = [
      {
        trip_id: "rush",
        scheduled: "2024-01-01T08:05:00Z",
        delay_min: -2,
        expected: "2024-01-01T08:03:00Z",
      },
      {
        trip_id: "late",
        scheduled: "2024-01-01T08:20:00Z",
        delay_min: 4,
        expected: "2024-01-01T08:24:00Z",
      },
    ];
    vi.spyOn(api, "departures").mockResolvedValue(result(departures));

    renderWithClient(
      <DeparturesPanel
        routeId="T4"
        stopId="S1"
        selectedDeparture={null}
        onSelectDeparture={() => {}}
        now={now}
      />,
    );

    await screen.findByText("-2 min");
    expect(screen.getByText("-2 min")).toHaveClass("text-yellow-500");
    expect(screen.getByText("+4 min")).toHaveClass("text-red-600");
  });
});
