import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { within } from "@testing-library/react";

import ArrivalTimeline, { formatArrival } from "./ArrivalTimeline";
import { api } from "../lib/api";
import type { ApiResult } from "../lib/api";
import type { Departure, TimelineNode } from "../lib/types";

const status = {
  source: "realtime" as const,
  fetched_at: "2024-01-01T00:00:00Z",
  stale: false,
};

function result(data: TimelineNode[]): ApiResult<TimelineNode[]> {
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

describe("formatArrival", () => {
  it("formats an ISO timestamp as HH:MM local time", () => {
    // Build an ISO string from a local time so the assertion is TZ-agnostic.
    const local = new Date(2024, 0, 1, 9, 5);
    const formatted = formatArrival(local.toISOString());
    expect(formatted).toMatch(/\b\d{1,2}[:.]\d{2}\b/);
  });

  it("falls back to the raw string when it can't be parsed", () => {
    expect(formatArrival("not-a-date")).toBe("not-a-date");
  });
});

describe("ArrivalTimeline", () => {
  it("prompts to select a line and stop when nothing is selected (R7)", () => {
    const spy = vi.spyOn(api, "timeline");

    renderWithClient(<ArrivalTimeline routeId={null} stopId={null} />);

    expect(screen.getByText(/select a line and stop/i)).toBeInTheDocument();
    // No request should be made without a selection.
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders the current stop first followed by downstream stops (R7.1, R7.2, R7.3)", async () => {
    const nodes: TimelineNode[] = [
      {
        stop_id: "S1",
        name: "Deák Ferenc tér",
        stop_sequence: 5,
        expected_arrival: "2024-01-01T09:00:00Z",
        is_current: true,
      },
      {
        stop_id: "S2",
        name: "Astoria",
        stop_sequence: 6,
        expected_arrival: "2024-01-01T09:04:00Z",
        is_current: false,
      },
      {
        stop_id: "S3",
        name: "Blaha Lujza tér",
        stop_sequence: 7,
        expected_arrival: "2024-01-01T09:08:00Z",
        is_current: false,
      },
    ];
    vi.spyOn(api, "timeline").mockResolvedValue(result(nodes));

    renderWithClient(<ArrivalTimeline routeId="T4" stopId="S1" />);

    await waitFor(() => {
      expect(screen.getByText("Deák Ferenc tér")).toBeInTheDocument();
    });

    // All stop names render.
    expect(screen.getByText("Astoria")).toBeInTheDocument();
    expect(screen.getByText("Blaha Lujza tér")).toBeInTheDocument();

    // The first list item is the current stop (R7.1).
    const items = screen.getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Deák Ferenc tér");
    expect(items[0]).toHaveTextContent(/current stop/i);

    // Order matches the delivered stop_sequence order (R7.2).
    expect(items[1]).toHaveTextContent("Astoria");
    expect(items[2]).toHaveTextContent("Blaha Lujza tér");
  });

  it("requests the timeline for the selected route and stop", async () => {
    const spy = vi.spyOn(api, "timeline").mockResolvedValue(result([]));

    renderWithClient(<ArrivalTimeline routeId="T4" stopId="S1" />);

    await waitFor(() => {
      // No departure selected -> no trip anchoring (trip_id is undefined).
      expect(spy).toHaveBeenCalledWith("T4", "S1", undefined);
    });
  });

  it("shows only the current stop when there are no downstream stops (R7.4)", async () => {
    const nodes: TimelineNode[] = [
      {
        stop_id: "S9",
        name: "Terminus",
        stop_sequence: 42,
        expected_arrival: "2024-01-01T10:00:00Z",
        is_current: true,
      },
    ];
    vi.spyOn(api, "timeline").mockResolvedValue(result(nodes));

    renderWithClient(<ArrivalTimeline routeId="B7" stopId="S9" />);

    await waitFor(() => {
      expect(screen.getByText("Terminus")).toBeInTheDocument();
    });

    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent(/current stop/i);
  });

  it("shows an empty state when the timeline has no nodes", async () => {
    vi.spyOn(api, "timeline").mockResolvedValue(result([]));

    renderWithClient(<ArrivalTimeline routeId="B7" stopId="S9" />);

    await waitFor(() => {
      expect(screen.getByText(/no upcoming arrivals/i)).toBeInTheDocument();
    });
  });

  it("renders a degraded state when the backend is unreachable (R10.3)", async () => {
    vi.spyOn(api, "timeline").mockRejectedValue(new Error("network down"));

    renderWithClient(<ArrivalTimeline routeId="B7" stopId="S9" />);

    await waitFor(() => {
      expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
    });
  });

  describe("selected departure drives the current node", () => {
    // Under trip anchoring, selecting a departure refetches the timeline
    // anchored on that trip, so the head node carries that trip's own time.
    const DEFAULT_HEAD_ARRIVAL = new Date(2024, 0, 1, 9, 0).toISOString();
    const TRIP_HEAD_ARRIVAL = new Date(2024, 0, 1, 9, 35).toISOString();

    function headNode(expectedArrival: string): TimelineNode {
      return {
        stop_id: "S1",
        name: "Deák Ferenc tér",
        stop_sequence: 5,
        expected_arrival: expectedArrival,
        is_current: true,
      };
    }

    function departure(delayMin: number): Departure {
      return {
        trip_id: "trip-x",
        scheduled: new Date(2024, 0, 1, 9, 30).toISOString(),
        delay_min: delayMin,
        expected: TRIP_HEAD_ARRIVAL,
      };
    }

    it("anchors the timeline fetch on the selected departure's trip", async () => {
      const spy = vi
        .spyOn(api, "timeline")
        .mockResolvedValue(result([headNode(TRIP_HEAD_ARRIVAL)]));

      renderWithClient(
        <ArrivalTimeline
          routeId="T4"
          stopId="S1"
          selectedDeparture={departure(0)}
        />,
      );

      await screen.findByText("Deák Ferenc tér");
      // The trip_id is forwarded so the backend anchors on that exact trip.
      expect(spy).toHaveBeenCalledWith("T4", "S1", "trip-x");
    });

    it("shows the selected trip's head arrival time", async () => {
      vi.spyOn(api, "timeline").mockResolvedValue(
        result([headNode(TRIP_HEAD_ARRIVAL)]),
      );

      renderWithClient(
        <ArrivalTimeline
          routeId="T4"
          stopId="S1"
          selectedDeparture={departure(0)}
        />,
      );

      await screen.findByText("Deák Ferenc tér");
      const head = screen.getAllByRole("listitem")[0];
      expect(
        within(head).getByText(formatArrival(TRIP_HEAD_ARRIVAL)),
      ).toBeInTheDocument();
    });

    it("colors a late selected departure red", async () => {
      vi.spyOn(api, "timeline").mockResolvedValue(
        result([headNode(TRIP_HEAD_ARRIVAL)]),
      );

      renderWithClient(
        <ArrivalTimeline
          routeId="T4"
          stopId="S1"
          selectedDeparture={departure(5)}
        />,
      );

      await screen.findByText("Deák Ferenc tér");
      const head = screen.getAllByRole("listitem")[0];
      expect(
        within(head).getByText(formatArrival(TRIP_HEAD_ARRIVAL)),
      ).toHaveClass("text-red-600");
    });

    it("colors a rush (early) selected departure yellow", async () => {
      vi.spyOn(api, "timeline").mockResolvedValue(
        result([headNode(TRIP_HEAD_ARRIVAL)]),
      );

      renderWithClient(
        <ArrivalTimeline
          routeId="T4"
          stopId="S1"
          selectedDeparture={departure(-3)}
        />,
      );

      await screen.findByText("Deák Ferenc tér");
      const head = screen.getAllByRole("listitem")[0];
      expect(
        within(head).getByText(formatArrival(TRIP_HEAD_ARRIVAL)),
      ).toHaveClass("text-yellow-500");
    });

    it("falls back to the node's own time and default color when nothing is selected", async () => {
      vi.spyOn(api, "timeline").mockResolvedValue(
        result([headNode(DEFAULT_HEAD_ARRIVAL)]),
      );

      renderWithClient(<ArrivalTimeline routeId="T4" stopId="S1" />);

      await screen.findByText("Deák Ferenc tér");
      const head = screen.getAllByRole("listitem")[0];
      const time = within(head).getByText(
        formatArrival(DEFAULT_HEAD_ARRIVAL),
      );
      expect(time).toHaveClass("text-slate-600");
    });
  });
});
