/**
 * QuickGuide — a small "?" help control for the header.
 *
 * Hovering (or keyboard-focusing) the button reveals a popover with a short,
 * bulleted walkthrough of how to use the dashboard. It is intentionally
 * self-contained and presentational: no data, no props required.
 *
 * Accessibility:
 *   - The popover opens on BOTH pointer hover and keyboard focus, so it is
 *     reachable without a mouse (hover-only tooltips are a common a11y gap).
 *   - The trigger is a real <button> with `aria-expanded` / `aria-describedby`,
 *     and the popover is linked by id so screen readers announce it.
 *   - Escape closes it and returns focus to the trigger.
 */
import { useId, useRef, useState } from "react";

import { useT } from "../lib/i18n";

/** Ordered translation keys for the walkthrough. Edit copy in i18n.tsx. */
const GUIDE_STEP_KEYS: string[] = [
  "guide.step.pickLine",
  "guide.step.flip",
  "guide.step.pickStop",
  "guide.step.readPrediction",
  "guide.step.pickDestination",
  "guide.step.pickDeparture",
  "guide.step.explore",
  "guide.step.badge",
];

export default function QuickGuide() {
  const t = useT();
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const buttonRef = useRef<HTMLButtonElement>(null);

  function close() {
    setOpen(false);
  }

  return (
    <div
      className="relative inline-flex"
      // Pointer hover opens/closes for mouse users...
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={close}
      // ...and focus-within keeps it open while the button (or its contents)
      // is keyboard-focused.
      onFocus={() => setOpen(true)}
      onBlur={(event) => {
        // Close only when focus leaves the whole widget, not when moving within.
        if (!event.currentTarget.contains(event.relatedTarget as Node)) {
          close();
        }
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          close();
          buttonRef.current?.focus();
        }
      }}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-label={t("guide.button")}
        aria-expanded={open}
        aria-describedby={open ? panelId : undefined}
        className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-slate-300 bg-white text-sm font-semibold text-slate-600 transition hover:border-slate-400 hover:bg-slate-50 hover:text-slate-800 focus:outline-none focus:ring-2 focus:ring-slate-400"
      >
        <span aria-hidden="true">?</span>
      </button>

      {open && (
        <div
          id={panelId}
          role="tooltip"
          className="absolute left-0 top-full z-[1000] mt-2 w-80 max-w-[90vw] rounded-lg border border-slate-200 bg-white p-4 text-left text-sm text-slate-700 shadow-lg"
        >
          <p className="mb-2 font-semibold text-slate-900">
            {t("guide.title")}
          </p>
          <ol className="list-decimal space-y-1.5 pl-5 marker:text-slate-400">
            {GUIDE_STEP_KEYS.map((key) => (
              <li key={key}>{t(key)}</li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
