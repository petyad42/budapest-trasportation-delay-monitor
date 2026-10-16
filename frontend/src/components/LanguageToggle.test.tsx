import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import LanguageToggle from "./LanguageToggle";
import { LanguageProvider, useLanguage } from "../lib/i18n";

/** Probe that renders a translated string so we can assert the active language. */
function Probe() {
  const { t } = useLanguage();
  return <p data-testid="probe">{t("section.chooseRoute")}</p>;
}

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
});

describe("LanguageToggle", () => {
  it("renders EN and HU segments with the initial language pressed", () => {
    render(
      <LanguageProvider initialLang="en">
        <LanguageToggle />
      </LanguageProvider>,
    );

    const en = screen.getByRole("button", { name: /switch to english/i });
    const hu = screen.getByRole("button", { name: /switch to hungarian/i });
    expect(en).toHaveAttribute("aria-pressed", "true");
    expect(hu).toHaveAttribute("aria-pressed", "false");
  });

  it("switches the app language to Hungarian when HU is clicked", async () => {
    const user = userEvent.setup();
    render(
      <LanguageProvider initialLang="en">
        <LanguageToggle />
        <Probe />
      </LanguageProvider>,
    );

    // Starts English.
    expect(screen.getByTestId("probe")).toHaveTextContent("Choose a route");

    await user.click(screen.getByRole("button", { name: /switch to hungarian/i }));

    // Now Hungarian, and the HU segment is pressed. After the switch the
    // control's own aria-labels are Hungarian too ("Váltás magyarra" = switch
    // to Hungarian), so re-query by the Hungarian name.
    expect(screen.getByTestId("probe")).toHaveTextContent(
      "Útvonal kiválasztása",
    );
    expect(
      screen.getByRole("button", { name: /váltás magyarra/i }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("persists the chosen language to localStorage", async () => {
    const user = userEvent.setup();
    render(
      <LanguageProvider initialLang="en">
        <LanguageToggle />
      </LanguageProvider>,
    );

    await user.click(screen.getByRole("button", { name: /switch to hungarian/i }));
    expect(window.localStorage.getItem("btm.lang")).toBe("hu");
  });
});
