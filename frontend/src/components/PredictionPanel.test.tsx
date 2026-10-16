import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import PredictionPanel, {
  formatAdjustment,
  formatMinutes,
} from "./PredictionPanel";
import { api } from "../lib/api";
import type { ApiResult } from "../lib/api";
import type { Prediction } from "../lib/types";

const status = {
  source: "realtime" as const,
  fetched_at: "2024-01-01T00:00:00Z",
  stale: false,
};

function result(data: Prediction): ApiResult<Prediction> {
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

describe("formatMinutes / formatAdjustment", () => {
  it("formats minutes to one decimal", () => {
    expect(formatMinutes(12.34)).toBe("12.3 min");
    expect(formatMinutes(10)).toBe("10 min");
  });

  it("describes the adjustment sign", () => {
    expect(formatAdjustment(0)).toBe("no change vs scheduled");
    expect(formatAdjustment(2.5)).toBe("+2.5 min slower than scheduled");
    expect(formatAdjustment(-1)).toBe("-1 min faster than scheduled");
  });
});

describe("PredictionPanel", () => {
  it("shows a prompt state when no line/stop is selected", () => {
    const spy = vi.spyOn(api, "predict");

    renderWithClient(
      <PredictionPanel routeId={null} stopId={null} selectedDestination={null} />,
    );

    expect(
      screen.getByText(/select a line and stop/i),
    ).toBeInTheDocument();
    // No request should be made until both ids are present.
    expect(spy).not.toHaveBeenCalled();
  });

  it("renders the plain-language reliability verdict when available (R5.3, R5.4)", async () => {
    const prediction: Prediction = {
      expected_travel_min: 14.2,
      scheduled_travel_min: 12,
      adjustment_min: 2.2,
      weather_used: true,
      available: true,
    };
    vi.spyOn(api, "predict").mockResolvedValue(result(prediction));

    renderWithClient(
      <PredictionPanel routeId="T4" stopId="S1" selectedDestination={null} />,
    );

    // The Reliability block reframes the +2.2 min adjustment as a plain-language
    // verdict via the predictionText helpers.
    await waitFor(() => {
      expect(screen.getByTestId("reliability-verdict")).toBeInTheDocument();
    });
    expect(screen.getByText("Running ~2 min behind")).toBeInTheDocument();
    expect(
      screen.getByText("~2 min slower than scheduled"),
    ).toBeInTheDocument();
    // Weather was used, so no missing-weather note.
    expect(
      screen.queryByTestId("weather-missing-note"),
    ).not.toBeInTheDocument();
  });

  it("renders the verdict even when weather_used=false (R5.5)", async () => {
    const prediction: Prediction = {
      expected_travel_min: 15.5,
      scheduled_travel_min: 13,
      adjustment_min: 2.5,
      weather_used: false,
      available: true,
    };
    vi.spyOn(api, "predict").mockResolvedValue(result(prediction));

    renderWithClient(
      <PredictionPanel routeId="T4" stopId="S1" selectedDestination={null} />,
    );

    // The verdict must still render...
    await waitFor(() => {
      expect(screen.getByTestId("reliability-verdict")).toBeInTheDocument();
    });
    expect(screen.getByText("Running ~3 min behind")).toBeInTheDocument();
    expect(
      screen.getByText("~3 min slower than scheduled"),
    ).toBeInTheDocument();
    // ...and it must NOT be treated as unavailable.
    expect(screen.queryByText(/prediction unavailable/i)).not.toBeInTheDocument();
    // A note about the missing weather input is shown (R5.5 MAY clause).
    expect(screen.getByTestId("weather-missing-note")).toBeInTheDocument();
  });

  it("shows 'prediction unavailable' only when available=false (R5.6)", async () => {
    const prediction: Prediction = {
      expected_travel_min: 0,
      scheduled_travel_min: 0,
      adjustment_min: 0,
      weather_used: false,
      available: false,
    };
    vi.spyOn(api, "predict").mockResolvedValue(result(prediction));

    renderWithClient(
      <PredictionPanel routeId="T4" stopId="S1" selectedDestination={null} />,
    );

    await waitFor(() => {
      expect(screen.getByText(/prediction unavailable/i)).toBeInTheDocument();
    });
    expect(screen.queryByTestId("reliability-verdict")).not.toBeInTheDocument();
  });

  it("renders a degraded state when the backend is unreachable (R10.3)", async () => {
    vi.spyOn(api, "predict").mockRejectedValue(new Error("network down"));

    renderWithClient(
      <PredictionPanel routeId="T4" stopId="S1" selectedDestination={null} />,
    );

    await waitFor(() => {
      expect(
        screen.getByText(/prediction is currently unavailable/i),
      ).toBeInTheDocument();
    });
  });
});
