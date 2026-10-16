import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import QuickGuide from "./QuickGuide";

describe("QuickGuide", () => {
  it("renders a help trigger button", () => {
    render(<QuickGuide />);
    expect(
      screen.getByRole("button", { name: /quick guide/i }),
    ).toBeInTheDocument();
  });

  it("hides the guide until interacted with", () => {
    render(<QuickGuide />);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /quick guide/i }),
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("reveals bulleted steps on pointer hover", async () => {
    const user = userEvent.setup();
    render(<QuickGuide />);

    await user.hover(screen.getByRole("button", { name: /quick guide/i }));

    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip).toBeInTheDocument();
    // Steps render as list items.
    expect(screen.getAllByRole("listitem").length).toBeGreaterThan(1);
    expect(screen.getByText(/pick a line/i)).toBeInTheDocument();
  });

  it("reveals the guide on keyboard focus (not mouse-only)", async () => {
    const user = userEvent.setup();
    render(<QuickGuide />);

    await user.tab(); // focus the trigger
    expect(screen.getByRole("button", { name: /quick guide/i })).toHaveFocus();
    expect(await screen.findByRole("tooltip")).toBeInTheDocument();
  });

  it("closes on Escape and keeps focus on the trigger", async () => {
    const user = userEvent.setup();
    render(<QuickGuide />);

    await user.tab();
    expect(await screen.findByRole("tooltip")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /quick guide/i })).toHaveFocus();
  });

  it("opens the guide when the trigger is clicked (click focuses it)", async () => {
    const user = userEvent.setup();
    render(<QuickGuide />);
    const button = screen.getByRole("button", { name: /quick guide/i });

    await user.click(button);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
  });
});
