/**
 * LanguageToggle — the HU/EN switch on the right of the header.
 *
 * Renders a small segmented control with "EN" and "HU"; the active language is
 * highlighted and marked `aria-pressed`. Clicking either segment sets that
 * language (clicking the active one is a no-op). The choice persists via the
 * LanguageProvider (localStorage), so it survives reloads.
 */
import { useLanguage, type Lang } from "../lib/i18n";

const OPTIONS: { value: Lang; label: string }[] = [
  { value: "en", label: "EN" },
  { value: "hu", label: "HU" },
];

export default function LanguageToggle() {
  const { lang, setLang, t } = useLanguage();

  return (
    <div
      role="group"
      aria-label={t("header.langLabel")}
      className="inline-flex overflow-hidden rounded-lg border border-slate-300 text-sm font-semibold"
    >
      {OPTIONS.map((option) => {
        const active = lang === option.value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            aria-label={
              option.value === "hu"
                ? t("header.switchToHu")
                : t("header.switchToEn")
            }
            onClick={() => setLang(option.value)}
            className={`px-2.5 py-1 transition focus:outline-none focus:ring-2 focus:ring-slate-400 ${
              active
                ? "bg-slate-800 text-white"
                : "bg-white text-slate-600 hover:bg-slate-50"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
