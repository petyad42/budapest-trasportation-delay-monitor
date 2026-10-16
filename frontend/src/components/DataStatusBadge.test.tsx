import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { DataStatusBadge } from "./DataStatusBadge";
import type { DataStatus } from "../lib/types";

// Component tests for the global data-status indicator.
// Requirement 10.1: green = real-time, orange = mock.
// Requirement 10.2: re-rendering with a new source flips the badge.
// Requirement 10.3: null status (backend unreachable) renders a fallback.

const realtimeStatus: DataStatus = {
  source: "realtime",
  fetched_at: "2024-01-01T00:00:00Z",
  stale: false,
};

const mockStatus: DataStatus = {
  source: "mock",
  fetched_at: "2024-01-01T00:00:00Z",
  stale: false,
};

describe("DataStatusBadge", () => {
  it("shows the green real-time state when source is realtime", () => {
    render(<DataStatusBadge status={realtimeStatus} />);

    const badge = screen.getByRole("status");
    expect(badge).toHaveAttribute("data-variant", "realtime");
    expect(badge).toHaveTextContent(/real-time/i);
    expect(badge.className).toContain("green");
  });

  it("shows the orange mock state when source is mock", () => {
    render(<DataStatusBadge status={mockStatus} />);

    const badge = screen.getByRole("status");
    expect(badge).toHaveAttribute("data-variant", "mock");
    expect(badge).toHaveTextContent(/mock/i);
    expect(badge.className).toContain("orange");
  });

  it("reflects a source transition when re-rendered (Requirement 10.2)", () => {
    const { rerender } = render(<DataStatusBadge status={realtimeStatus} />);
    expect(screen.getByRole("status")).toHaveAttribute(
      "data-variant",
      "realtime",
    );

    rerender(<DataStatusBadge status={mockStatus} />);
    expect(screen.getByRole("status")).toHaveAttribute("data-variant", "mock");
  });

  it("renders an unavailable fallback when status is null (Requirement 10.3)", () => {
    render(<DataStatusBadge status={null} />);

    const badge = screen.getByRole("status");
    expect(badge).toHaveAttribute("data-variant", "unavailable");
    expect(badge).toHaveTextContent(/unavailable/i);
  });

  it("annotates a stale realtime status", () => {
    render(<DataStatusBadge status={{ ...realtimeStatus, stale: true }} />);

    const badge = screen.getByRole("status");
    expect(badge).toHaveAttribute("data-variant", "realtime");
    expect(badge).toHaveTextContent(/stale/i);
  });
});
