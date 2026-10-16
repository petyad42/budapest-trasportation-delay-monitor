import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ProblematicRoutesList, {
  formatDelayMetric,
} from "./ProblematicRoutesList";
import { api } from "../lib/api";
import type { ApiResult } from "../lib/api";
import type { ProblemScore } from "../lib/types";

const status = {
  source: "realtime" as const,
  fetched_at: "2024-01-01T00:00:00Z",
  stale: false,
};

function result(data: ProblemScore[]): ApiResult<ProblemScore[]> {
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

describe("formatDelayMetric", () => {
  it("renders positive delays as 'N min late' (R3.3)", () => {
    expect(formatDelayMetric(4.25)).toBe("4.3 min late");
    expect(formatDelayMetric(2)).toBe("2 min late");
  });

  it("renders zero / negative delays as 'on time'", () => {
    expect(formatDelayMetric(0)).toBe("on time");
    expect(formatDelayMetric(-1.5)).toBe("on time");
  });
});

describe("ProblematicRoutesList", () => {
  it("shows each route's identifier, mode, and delay metric (R3.2, R3.3)", async () => {
    const routes: ProblemScore[] = [
      {
        route_id: "T4",
        short_name: "4",
        mode: "tram",
        score: 0.9,
        avg_delay_min: 6.1,
      },
      {
        route_id: "B7",
        short_name: "7",
        mode: "bus",
        score: 0.5,
        avg_delay_min: 3,
      },
    ];
    vi.spyOn(api, "problematicRoutes").mockResolvedValue(result(routes));

    renderWithClient(<ProblematicRoutesList />);

    await waitFor(() => {
      expect(screen.getByText("4")).toBeInTheDocument();
    });
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("Tram")).toBeInTheDocument();
    expect(screen.getByText("Bus")).toBeInTheDocument();
    expect(screen.getByText("6.1 min late")).toBeInTheDocument();
    expect(screen.getByText("3 min late")).toBeInTheDocument();
  });

  it("requests exactly the top 5 (R3.2)", async () => {
    const spy = vi
      .spyOn(api, "problematicRoutes")
      .mockResolvedValue(result([]));

    renderWithClient(<ProblematicRoutesList />);

    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith(5);
    });
  });

  it("shows an empty state when no routes have delays (R3.5)", async () => {
    vi.spyOn(api, "problematicRoutes").mockResolvedValue(result([]));

    renderWithClient(<ProblematicRoutesList />);

    await waitFor(() => {
      expect(screen.getByText(/no delayed routes/i)).toBeInTheDocument();
    });
  });

  it("re-polls on the configured interval so the ranking refreshes (R3.4)", async () => {
    vi.useFakeTimers();
    try {
      const spy = vi
        .spyOn(api, "problematicRoutes")
        .mockResolvedValue(result([]));

      renderWithClient(<ProblematicRoutesList pollIntervalMs={1000} />);

      await vi.waitFor(() => {
        expect(spy).toHaveBeenCalledTimes(1);
      });

      await vi.advanceTimersByTimeAsync(1000);
      expect(spy.mock.calls.length).toBeGreaterThanOrEqual(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("renders a degraded state when the backend is unreachable (R10.3)", async () => {
    vi.spyOn(api, "problematicRoutes").mockRejectedValue(
      new Error("network down"),
    );

    renderWithClient(<ProblematicRoutesList />);

    await waitFor(() => {
      expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
    });
  });
});
