import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { WeatherWidget } from "./WeatherWidget";
import type { Weather } from "../lib/types";

// Component tests for the header weather widget.
// Requirement 8.2: display current temperature + condition when available.
// Requirement 8.3: render a graceful fallback when weather is unavailable.

const weather: Weather = {
  temperature_c: 12.4,
  condition: "Partly cloudy",
  weather_code: 2,
  wind_kph: 8,
  precipitation_mm: 0,
};

describe("WeatherWidget", () => {
  it("shows the temperature and condition when weather is available", () => {
    render(<WeatherWidget weather={weather} />);

    const widget = screen.getByLabelText(/current weather/i);
    expect(widget).toHaveAttribute("data-state", "available");
    // Rounded to a whole degree for the compact header display.
    expect(widget).toHaveTextContent("12°C");
    expect(widget).toHaveTextContent("Partly cloudy");
  });

  it("renders a graceful fallback when weather is null (Requirement 8.3)", () => {
    render(<WeatherWidget weather={null} />);

    const fallback = screen.getByRole("status");
    expect(fallback).toHaveAttribute("data-state", "unavailable");
    expect(fallback).toHaveTextContent(/weather unavailable/i);
  });
});
