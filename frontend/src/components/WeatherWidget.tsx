/**
 * WeatherWidget — current temperature and condition (top-right of the header).
 *
 * Requirement 8.1/8.2: display the current temperature and a weather condition
 * summary in a dedicated widget.
 * Requirement 8.3: when weather data is unavailable (the backend returns
 * `null`, or the request failed), render a graceful fallback state instead of
 * blank content.
 *
 * This component is presentational: the Dashboard (task 22) fetches
 * `/api/weather` and passes the unwrapped `Weather` value, which is `null` when
 * the upstream weather source is unavailable.
 */
import type { Weather } from "../lib/types";
import { useT } from "../lib/i18n";

export interface WeatherWidgetProps {
  /** Current weather, or `null` when weather is unavailable (Requirement 8.3). */
  weather: Weather | null;
}

/** Round to a whole degree for a compact header display. */
function formatTemperature(temperatureC: number): string {
  return `${Math.round(temperatureC)}°C`;
}

export function WeatherWidget({ weather }: WeatherWidgetProps) {
  const t = useT();
  if (weather == null) {
    return (
      <div
        role="status"
        aria-label={t("weather.ariaUnavailable")}
        data-state="unavailable"
        className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-1 text-sm text-slate-500"
      >
        <span aria-hidden="true">🌡️</span>
        {t("weather.unavailable")}
      </div>
    );
  }

  return (
    <div
      aria-label={t("weather.ariaCurrent", { temp: formatTemperature(weather.temperature_c), condition: weather.condition })}
      data-state="available"
      className="inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-1 text-sm text-slate-800"
    >
      <span className="font-semibold">
        {formatTemperature(weather.temperature_c)}
      </span>
      <span className="text-slate-600">{weather.condition}</span>
    </div>
  );
}

export default WeatherWidget;
