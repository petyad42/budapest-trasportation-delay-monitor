import { describe, expect, it } from "vitest";

import {
  BUS_COLOR,
  METRO_FALLBACK_COLOR,
  TRAM_COLOR,
  TROLLEYBUS_COLOR,
  colorForMode,
  normalizeHex,
} from "./colors";

// These mirror backend/colors.py; the tests lock the values so the two modules
// cannot silently drift (Requirement 2.2, design "Property 8").
describe("colorForMode", () => {
  it("maps the fixed-color modes to their canonical colors", () => {
    expect(colorForMode("bus")).toBe(BUS_COLOR);
    expect(colorForMode("tram")).toBe(TRAM_COLOR);
    expect(colorForMode("trolleybus")).toBe(TROLLEYBUS_COLOR);
  });

  it("ignores route_color for the fixed-color modes", () => {
    // A bus never adopts a route_color; it stays light blue.
    expect(colorForMode("bus", "FF0000")).toBe(BUS_COLOR);
  });

  it("uses the official route_color for metro when valid", () => {
    expect(colorForMode("metro", "E41F18")).toBe("#E41F18");
  });

  it("falls back to the metro default when route_color is missing/invalid", () => {
    expect(colorForMode("metro")).toBe(METRO_FALLBACK_COLOR);
    expect(colorForMode("metro", null)).toBe(METRO_FALLBACK_COLOR);
    expect(colorForMode("metro", "nothex")).toBe(METRO_FALLBACK_COLOR);
  });
});

describe("normalizeHex", () => {
  it("normalizes bare and hashed 6-digit hex to #RRGGBB uppercase", () => {
    expect(normalizeHex("ffd800")).toBe("#FFD800");
    expect(normalizeHex("#ffd800")).toBe("#FFD800");
  });

  it("returns null for invalid input", () => {
    expect(normalizeHex(null)).toBeNull();
    expect(normalizeHex("")).toBeNull();
    expect(normalizeHex("12345")).toBeNull();
    expect(normalizeHex("zzzzzz")).toBeNull();
  });
});
