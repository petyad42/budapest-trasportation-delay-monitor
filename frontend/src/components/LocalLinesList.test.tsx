import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import LocalLinesList from "./LocalLinesList";
import { api } from "../lib/api";
import type { ApiResult } from "../lib/api";
import type { Line } from "../lib/types";

const status = {
  source: "realtime" as const,
  fetched_at: "2024-01-01T00:00:00Z",
  stale: false,
};

function result(data: Line[]): ApiResult<Line[]> {
  return { data, status };
}

const LINES: Line[] = [
  { route_id: "T4", short_name: "4", mode: "tram", color: "" },
  { route_id: "B7", short_name: "7", mode: "bus", color: "" },
];

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

describe("LocalLinesList", () => {
  it("renders the local lines with their mode (R2.3)", async () => {
    vi.spyOn(api, "lines").mockResolvedValue(result(LINES));

    renderWithClient(
      <LocalLinesList selectedLine={null} onSelectLine={() => {}} />,
    );

    await waitFor(() => expect(screen.getByText("4")).toBeInTheDocument());
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("Tram")).toBeInTheDocument();
    expect(screen.getByText("Bus")).toBeInTheDocument();
  });

  it("lifts the clicked line to the parent for map highlight (R2.4)", async () => {
    vi.spyOn(api, "lines").mockResolvedValue(result(LINES));
    const onSelectLine = vi.fn();

    renderWithClient(
      <LocalLinesList selectedLine={null} onSelectLine={onSelectLine} />,
    );

    await waitFor(() => expect(screen.getByText("4")).toBeInTheDocument());
    await userEvent.click(screen.getByText("4"));

    expect(onSelectLine).toHaveBeenCalledWith(LINES[0]);
  });

  it("clears the highlight when the selected line is clicked again", async () => {
    vi.spyOn(api, "lines").mockResolvedValue(result(LINES));
    const onSelectLine = vi.fn();

    renderWithClient(
      <LocalLinesList selectedLine={LINES[0]} onSelectLine={onSelectLine} />,
    );

    await waitFor(() => expect(screen.getByText("4")).toBeInTheDocument());
    await userEvent.click(screen.getByText("4"));

    expect(onSelectLine).toHaveBeenCalledWith(null);
  });

  it("marks the selected line as selected", async () => {
    vi.spyOn(api, "lines").mockResolvedValue(result(LINES));

    renderWithClient(
      <LocalLinesList selectedLine={LINES[1]} onSelectLine={() => {}} />,
    );

    await waitFor(() => expect(screen.getByText("7")).toBeInTheDocument());
    const options = screen.getAllByRole("option");
    const selected = options.find(
      (o) => o.getAttribute("aria-selected") === "true",
    );
    expect(selected).toBeDefined();
    expect(selected!).toHaveTextContent("7");
  });

  it("shows an empty state when there are no lines", async () => {
    vi.spyOn(api, "lines").mockResolvedValue(result([]));

    renderWithClient(
      <LocalLinesList selectedLine={null} onSelectLine={() => {}} />,
    );

    await waitFor(() =>
      expect(screen.getByText(/no local lines/i)).toBeInTheDocument(),
    );
  });

  it("renders a degraded state when the backend is unreachable (R10.3)", async () => {
    vi.spyOn(api, "lines").mockRejectedValue(new Error("network down"));

    renderWithClient(
      <LocalLinesList selectedLine={null} onSelectLine={() => {}} />,
    );

    await waitFor(() =>
      expect(screen.getByText(/unavailable/i)).toBeInTheDocument(),
    );
  });
});
