import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LanguageProvider, makeTranslate, useLanguage } from "./i18n";

describe("makeTranslate", () => {
  it("returns the language string for a known key", () => {
    expect(makeTranslate("hu")("section.chooseRoute")).toBe(
      "Útvonal kiválasztása",
    );
    expect(makeTranslate("en")("section.chooseRoute")).toBe("Choose a route");
  });

  it("interpolates {placeholders}", () => {
    expect(makeTranslate("en")("pt.inN", { n: 7 })).toBe("in 7 min");
    expect(makeTranslate("hu")("pt.inN", { n: 7 })).toBe("7 perc múlva");
  });

  it("falls back to English, then to the raw key, for missing entries", () => {
    // A key that exists only conceptually — unknown keys echo back.
    expect(makeTranslate("hu")("does.not.exist")).toBe("does.not.exist");
  });
});

describe("useLanguage", () => {
  function Probe() {
    const { t, lang } = useLanguage();
    return (
      <p data-testid="p">
        {lang}:{t("prediction.title")}
      </p>
    );
  }

  it("provides the active language's translator", () => {
    render(
      <LanguageProvider initialLang="hu">
        <Probe />
      </LanguageProvider>,
    );
    expect(screen.getByTestId("p")).toHaveTextContent("hu:Előrejelzés");
  });

  it("falls back to English when used outside a provider", () => {
    render(<Probe />);
    expect(screen.getByTestId("p")).toHaveTextContent("en:Prediction");
  });
});
