import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import JourneyEstimateBlock, {
  computeJourneyMinutes,
  resolveOriginNode,
} from "./JourneyEstimateBlock";
import ArrivalTimeline from "./ArrivalTimeline";
import { api } from "../lib/api";
import type { ApiResult } from "../lib/api";
import type { TimelineNode } from "../lib/types";

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

const NODES: TimelineNode[] = [
  {
    stop_id: "S1",
    name: "Origin",
    stop_sequence: 0,
    expected_arrival: "2024-01-01T08:00:00Z",
    is_current: true,
  },
  {
    stop_id: "S2",
    name: "Middle",
    stop_sequence: 1,
    expected_arrival: "2024-01-01T08:10:00Z",
    is_current: false,
  },
  {
    stop_id: "S3",
    name: "Destination",
    stop_sequence: 2,
    expected_arrival: "2024-01-01T08:16:00Z",
    is_current: false,
  },
];

afterEach(() => {
  vi.restoreAllMocks();
});

describe("pure helpers", () => {
  it("resolves the is_current node as origin", () => {
    expect(resolveOriginNode(NODES, "S1")?.stop_id).toBe("S1");
  });

  it("computes whole-minute duration origin -> destination", () => {
    const origin = resolveOriginNode(NODES, "S1");
    expect(computeJourneyMinutes(NODES, origin, "S3")).toBe(16);
  });

  it("returns null when the destination is absent", () => {
    const origin = resolveOriginNode(NODES, "S1");
    expect(computeJourneyMinutes(NODES, origin, "NOPE")).toBeNull();
  });
});

describe("JourneyEstimateBlock query-key isolation (regression)", () => {
  it(
    "shows the journey duration even when ArrivalTimeline shares the same " +
      "route/stop under one QueryClient",
    async () => {
      // Both components fetch the timeline for the same route+stop. Before the
      // fix they shared the key ["timeline", routeId, stopId] but with
      // different queryFns (bare array vs full envelope), so whichever
      // registered first won and the journey block saw `undefined` data,
      // yielding the false "isn't on this trip" message. The journey block now
      // uses a distinct ["timeline","journey",...] key.
      const spy = vi
        .spyOn(api, "timeline")
        .mockResolvedValue(result(NODES));

      renderWithClient(
        <>
          <ArrivalTimeline routeId="T4" stopId="S1" />
          <JourneyEstimateBlock
            routeId="T4"
            stopId="S1"
            selectedDestination="S3"
          />
        </>,
      );

      await waitFor(() => {
        expect(screen.getByTestId("journey-duration")).toBeInTheDocument();
      });
      expect(screen.getByText("16 min")).toBeInTheDocument();
      expect(
        screen.queryByText(/isn.t on this trip/i),
      ).not.toBeInTheDocument();
      expect(spy).toHaveBeenCalled();
    },
  );

  it("prompts to pick a destination when none is chosen", () => {
    renderWithClient(
      <JourneyEstimateBlock
        routeId="T4"
        stopId="S1"
        selectedDestination={null}
      />,
    );
    expect(screen.getByText(/pick a destination/i)).toBeInTheDocument();
  });
});
