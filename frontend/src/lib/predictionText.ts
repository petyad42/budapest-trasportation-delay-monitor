/**
 * Plain-language formatting and verdict helpers for the Prediction panel.
 *
 * This is a pure, React-free module (design: "predictionText.ts (new, pure, no
 * React)"). Every function is deterministic — the caller passes `now` so there
 * is no internal clock — which keeps the panel presentational and this logic
 * unit/property testable.
 *
 * Shared rules (Requirement 3):
 *   - The "on time / as scheduled" band uses magnitude strictly less than one
 *     minute, so sub-minute noise rounds away (R3.3).
 *   - No function ever emits a "-0" token; rounding normalizes -0 to 0 before
 *     formatting (R3.4).
 *   - `formatTimeUntil` returns the placeholder "—" for an invalid ISO value
 *     (R3.6) and "due" for a time at or before `now` (R3.5).
 */

import type { Translate } from "./i18n";

/** Dash placeholder used when a timestamp cannot be parsed (R3.6). */
const DASH = "\u2014"; // em dash "—"

/**
 * English fallback translator so these helpers stay usable (and unit/property
 * testable) without a React context. Mirrors the "pt.*" keys in i18n.tsx.
 */
const EN: Record<string, string> = {
  "pt.due": "due",
  "pt.in1": "in 1 min",
  "pt.inN": "in {n} min",
  "pt.min": "{n} min",
  "pt.delay.onTimeVerdict": "Running about on time",
  "pt.delay.behind": "Running ~{n} min behind",
  "pt.delay.ahead": "Running ~{n} min ahead",
  "pt.adj.asScheduled": "About as scheduled",
  "pt.adj.slower": "~{n} min slower than scheduled",
  "pt.adj.faster": "~{n} min faster than scheduled",
  "pt.short.onTime": "on time",
  "pt.short.late": "{n} min late",
  "pt.short.early": "{n} min early",
};

function fallback(key: string, vars?: Record<string, string | number>): string {
  const tmpl = EN[key] ?? key;
  if (!vars) return tmpl;
  return tmpl.replace(/\{(\w+)\}/g, (_m, name) =>
    name in vars ? String(vars[name]) : `{${name}}`,
  );
}

/** Resolve a translator: the provided one, or the English fallback. */
function resolve(t?: Translate): Translate {
  return t ?? fallback;
}

/**
 * Round to a whole integer while normalizing negative zero to positive zero.
 *
 * `Math.round(-0.2)` yields `-0`, which stringifies to "-0"; this helper keeps
 * the rest of the module from ever producing a "-0" token (R3.4).
 */
function roundNoNegZero(value: number): number {
  const rounded = Math.round(value);
  // `rounded + 0` turns -0 into 0 without affecting any other value.
  return rounded === 0 ? 0 : rounded;
}

/**
 * Whole-minute string, e.g. "16 min".
 *
 * Rounds to the nearest minute and never prints "-0" (R3.4).
 */
export function formatMinutesRounded(min: number, t?: Translate): string {
  return resolve(t)("pt.min", { n: roundNoNegZero(min) });
}

/**
 * Time-until a target timestamp, phrased for a rider.
 *
 *   - invalid / NaN target        -> "—"   (R3.6)
 *   - computed time-until <= 0    -> "due" (R3.5)
 *   - <= 1 minute remaining       -> "in 1 min"
 *   - otherwise                   -> "in N min"
 *
 * The caller supplies `now` (ms since epoch); the function holds no clock.
 */
export function formatTimeUntil(
  targetIso: string,
  now: number,
  t?: Translate,
): string {
  const tr = resolve(t);
  const target = new Date(targetIso).getTime();
  if (Number.isNaN(target)) return DASH;

  const diffMs = target - now;
  if (diffMs <= 0) return tr("pt.due");

  const minutes = diffMs / 60000;
  if (minutes <= 1) return tr("pt.in1");

  return tr("pt.inN", { n: roundNoNegZero(minutes) });
}

/**
 * Plain-language delay verdict (R3.1, R3.3).
 *
 *   - |delay| < 1   -> "Running about on time"
 *   - delay >= 1    -> "Running ~N min behind"
 *   - delay <= -1   -> "Running ~N min ahead"
 *
 * `N` is the whole-minute magnitude of the delay.
 */
export function delayVerdict(delayMin: number, t?: Translate): string {
  const tr = resolve(t);
  if (Math.abs(delayMin) < 1) return tr("pt.delay.onTimeVerdict");
  const n = roundNoNegZero(Math.abs(delayMin));
  return delayMin >= 1
    ? tr("pt.delay.behind", { n })
    : tr("pt.delay.ahead", { n });
}

/**
 * Plain-language schedule-adjustment verdict (R3.2).
 *
 *   - |adjustment| < 1  -> "About as scheduled"
 *   - adjustment >= 1   -> "~N min slower than scheduled"
 *   - adjustment <= -1  -> "~N min faster than scheduled"
 *
 * `N` is the whole-minute magnitude of the adjustment.
 */
export function adjustmentVerdict(adjustmentMin: number, t?: Translate): string {
  const tr = resolve(t);
  if (Math.abs(adjustmentMin) < 1) return tr("pt.adj.asScheduled");
  const n = roundNoNegZero(Math.abs(adjustmentMin));
  return adjustmentMin >= 1
    ? tr("pt.adj.slower", { n })
    : tr("pt.adj.faster", { n });
}

/**
 * Short delay label for the Next ride block (R3.1, R3.3).
 *
 *   - |delay| < 1  -> "on time"
 *   - delay >= 1   -> "N min late"
 *   - delay <= -1  -> "N min early"
 *
 * `N` is the whole-minute magnitude of the delay.
 */
export function formatDelayShort(delayMin: number, t?: Translate): string {
  const tr = resolve(t);
  if (Math.abs(delayMin) < 1) return tr("pt.short.onTime");
  const n = roundNoNegZero(Math.abs(delayMin));
  return delayMin >= 1
    ? tr("pt.short.late", { n })
    : tr("pt.short.early", { n });
}
