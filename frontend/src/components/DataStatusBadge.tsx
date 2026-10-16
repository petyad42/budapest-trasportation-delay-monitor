/**
 * DataStatusBadge — the global data-status indicator (top-left of the header).
 *
 * Requirement 10.1: surface whether the view is showing real-time (green) or
 * mock (orange) data.
 * Requirement 10.2: when a source transitions between real-time and mock the
 * indicator updates accordingly — this component is a pure function of the
 * `status` prop, so re-rendering with a new status flips the color/label.
 * Requirement 10.3: when the backend is unreachable the caller passes
 * `status={null}` (there is no envelope to read); the badge renders a neutral
 * "unavailable" state rather than blank content.
 *
 * This component is presentational: the Dashboard (task 22) fetches
 * `/api/status` and passes the unwrapped `DataStatus` (or `null` on error).
 */
import type { DataStatus } from "../lib/types";
import { useT } from "../lib/i18n";

export interface DataStatusBadgeProps {
  /** The current data status, or `null` when the backend is unreachable. */
  status: DataStatus | null;
}

type BadgeVariant = "realtime" | "mock" | "unavailable";

interface BadgeAppearance {
  /** Tailwind classes for the badge container. */
  className: string;
  /** Tailwind classes for the status dot. */
  dotClassName: string;
}

const APPEARANCE: Record<BadgeVariant, BadgeAppearance> = {
  realtime: {
    className: "border-green-300 bg-green-50 text-green-800",
    dotClassName: "bg-green-500",
  },
  mock: {
    className: "border-orange-300 bg-orange-50 text-orange-800",
    dotClassName: "bg-orange-500",
  },
  unavailable: {
    className: "border-slate-300 bg-slate-50 text-slate-600",
    dotClassName: "bg-slate-400",
  },
};

function variantFor(status: DataStatus | null): BadgeVariant {
  if (status == null) return "unavailable";
  return status.source === "realtime" ? "realtime" : "mock";
}

const LABEL_KEY: Record<BadgeVariant, string> = {
  realtime: "status.realtime",
  mock: "status.mock",
  unavailable: "status.unavailable",
};

export function DataStatusBadge({ status }: DataStatusBadgeProps) {
  const t = useT();
  const variant = variantFor(status);
  const appearance = APPEARANCE[variant];
  const stale = status?.stale ?? false;
  const label = t(LABEL_KEY[variant]);

  return (
    <span
      role="status"
      aria-label={`${t("status.ariaPrefix")}: ${label}`}
      data-variant={variant}
      className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium ${appearance.className}`}
    >
      <span
        aria-hidden="true"
        className={`h-2 w-2 rounded-full ${appearance.dotClassName}`}
      />
      {label}
      {stale ? (
        <span className="text-[0.65rem] opacity-70">{t("status.stale")}</span>
      ) : null}
    </span>
  );
}

export default DataStatusBadge;
