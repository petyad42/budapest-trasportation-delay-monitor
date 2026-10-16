// Global test setup for Vitest + React Testing Library.
//
// - Extends `expect` with jest-dom matchers (toBeInTheDocument, etc.).
// - Cleans up the DOM after each test so component tests don't leak state.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
