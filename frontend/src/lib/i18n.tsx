/**
 * Lightweight, dependency-free internationalization for the dashboard.
 *
 * Why not a library: the app has a small, fixed set of UI strings and only two
 * languages (English and Hungarian), so a full i18n framework would be more
 * machinery than the feature needs. This module provides just enough:
 *   - a `Lang` union ("en" | "hu");
 *   - a `LanguageProvider` that holds the active language, persists the choice
 *     to localStorage, and keeps the document's <html lang> in sync;
 *   - a `useLanguage` hook exposing `{ lang, setLang, toggle, t }`.
 *
 * `t(key, vars?)` looks up a dot-path in the active language's dictionary and
 * interpolates `{name}` placeholders. Missing keys fall back to English, then
 * to the raw key, so the UI never renders blank.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Lang = "en" | "hu";

const STORAGE_KEY = "btm.lang";

/**
 * The translation dictionaries. Keys are grouped by area and addressed with a
 * dot-path (e.g. `t("departures.title")`). Hungarian mirrors the English key
 * set exactly; keep them in lockstep when adding strings.
 */
const MESSAGES: Record<Lang, Record<string, string>> = {
  en: {
    "app.title": "Budapest Public Transport Delay Monitor",

    "header.langLabel": "Language",
    "header.switchToHu": "Switch to Hungarian",
    "header.switchToEn": "Switch to English",

    "banner.backendUnreachable":
      "The backend is currently unreachable — showing whatever data is available. Panels will recover automatically once the connection is restored.",

    "section.localLinesInfo": "Local lines info",
    "section.chooseRoute": "Choose a route",

    "status.realtime": "Real-time data",
    "status.mock": "Mock data",
    "status.unavailable": "Data unavailable",
    "status.stale": "(stale)",
    "status.ariaPrefix": "Data status",

    "weather.unavailable": "Weather unavailable",
    "weather.ariaUnavailable": "Weather unavailable",
    "weather.ariaCurrent": "Current weather: {temp}, {condition}",

    "guide.button": "Quick guide: how to use this site",
    "guide.title": "Quick guide",
    "guide.step.pickLine":
      "Pick a line in the “Choose a route” panel — type to search by number or name.",
    "guide.step.flip":
      "Use the ⇄ arrow to flip travel direction; the start and end termini swap.",
    "guide.step.pickStop":
      "Choose your stop — the list is scoped to the selected direction.",
    "guide.step.readPrediction":
      "Read the prediction panel: Next ride (time until departure), Journey estimate, and Reliability.",
    "guide.step.pickDestination":
      "Pick a destination to see the expected journey time to a downstream stop.",
    "guide.step.pickDeparture":
      "Click a departure to anchor the arrival timeline on that exact trip.",
    "guide.step.explore":
      "Explore the map and the Top 5 problematic routes on the left to spot delays at a glance.",
    "guide.step.badge":
      "The data badge (top-left) shows whether you’re seeing real-time or mock data.",

    "lines.title": "Local lines",
    "lines.ariaList": "Local lines",
    "lines.ariaAvailable": "Available lines",
    "lines.loading": "Loading local lines…",
    "lines.error": "Local lines are currently unavailable.",
    "lines.empty": "No local lines found.",

    "problematic.title": "Top 5 problematic routes",
    "problematic.loading": "Loading problematic routes…",
    "problematic.error": "Problematic routes are currently unavailable.",
    "problematic.empty": "No delayed routes right now.",

    "route.lineLabel": "Line",
    "route.searchLines": "Search lines…",
    "route.selectedLine": "Selected line:",
    "route.loadingLines": "Loading lines…",
    "route.linesError": "Could not load lines.",
    "route.flip": "Flip travel direction",
    "route.flipDisabled": "This line has one direction",
    "route.stopLabel": "Stop",
    "route.searchStops": "Search stops…",
    "route.selectStopFirst": "Select a line first",
    "route.selectedStop": "Selected stop:",
    "route.loadingStops": "Loading stops…",
    "route.stopsError": "Could not load stops.",
    "route.noStops": "No matching stops.",
    "route.noLines": "No matching lines.",
    "route.linesAvailable": "Available lines",
    "route.stopsList": "Line stops",

    "destination.label": "Destination",
    "destination.disabled": "Select a line and stop first",
    "destination.search": "Search destinations…",
    "destination.ariaList": "Downstream destinations",
    "destination.selected": "Destination:",
    "destination.none": "No downstream stops.",

    "departures.title": "Departures",
    "departures.aria": "Upcoming departures",
    "departures.prompt": "Select a line and stop to see departures.",
    "departures.loading": "Loading departures…",
    "departures.error": "Departures are currently unavailable.",
    "departures.empty": "No upcoming departures.",
    "departures.onTime": "on time",

    "prediction.title": "Prediction",
    "prediction.prompt": "Select a line and stop to see a prediction.",

    "nextride.title": "Next ride",
    "nextride.departs": "Next ride departs",
    "nextride.prompt": "Select a line and stop to see the next ride.",
    "nextride.loading": "Loading next ride…",
    "nextride.error": "Next ride is currently unavailable.",
    "nextride.empty": "No upcoming departures.",

    "journey.title": "Journey estimate",
    "journey.label": "Expected journey time",
    "journey.prompt": "Pick a destination to see the expected journey time.",
    "journey.loading": "Loading journey estimate…",
    "journey.error": "The journey estimate is currently unavailable.",
    "journey.notOnTrip": "That destination isn’t on this trip.",

    "reliability.title": "Reliability",
    "reliability.loading": "Loading reliability…",
    "reliability.error": "Prediction is currently unavailable.",
    "reliability.unavailable": "Prediction unavailable.",
    "reliability.weatherMissing":
      "Weather data unavailable — reliability based on transit data only.",

    "timeline.title": "Arrival timeline",
    "timeline.prompt": "Select a line and stop to see expected arrivals.",
    "timeline.loading": "Loading timeline…",
    "timeline.error": "The arrival timeline is currently unavailable.",
    "timeline.empty": "No upcoming arrivals for this stop.",
    "timeline.currentStop": "Current stop",

    "mode.bus": "Bus",
    "mode.tram": "Tram",
    "mode.trolleybus": "Trolleybus",
    "mode.metro": "Metro",

    // predictionText phrases (verdict/time-until). {n} is a whole number.
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
    "pt.metric.late": "{n} min late",
  },
  hu: {
    "app.title": "Budapesti Közlekedési Késésfigyelő",

    "header.langLabel": "Nyelv",
    "header.switchToHu": "Váltás magyarra",
    "header.switchToEn": "Váltás angolra",

    "banner.backendUnreachable":
      "A háttérrendszer jelenleg nem elérhető — az elérhető adatokat mutatjuk. A panelek automatikusan helyreállnak, amint a kapcsolat visszaáll.",

    "section.localLinesInfo": "Helyi járatok",
    "section.chooseRoute": "Útvonal kiválasztása",

    "status.realtime": "Valós idejű adatok",
    "status.mock": "Teszt adatok",
    "status.unavailable": "Nincs elérhető adat",
    "status.stale": "(elavult)",
    "status.ariaPrefix": "Adatállapot",

    "weather.unavailable": "Időjárás nem elérhető",
    "weather.ariaUnavailable": "Időjárás nem elérhető",
    "weather.ariaCurrent": "Aktuális időjárás: {temp}, {condition}",

    "guide.button": "Gyors útmutató: hogyan használd az oldalt",
    "guide.title": "Gyors útmutató",
    "guide.step.pickLine":
      "Válassz járatot az „Útvonal kiválasztása” panelen — gépelj a szám vagy név szerinti kereséshez.",
    "guide.step.flip":
      "A ⇄ nyíllal fordíthatod meg a menetirányt; a kezdő- és végállomás felcserélődik.",
    "guide.step.pickStop":
      "Válaszd ki a megállót — a lista a kiválasztott irányra szűkül.",
    "guide.step.readPrediction":
      "Olvasd el az előrejelzés panelt: Következő járat (indulásig hátralévő idő), Útidő becslés és Megbízhatóság.",
    "guide.step.pickDestination":
      "Válassz célállomást, hogy lásd a várható menetidőt egy lejjebbi megállóig.",
    "guide.step.pickDeparture":
      "Kattints egy indulásra, hogy az érkezési idővonalat arra a konkrét járatra rögzítsd.",
    "guide.step.explore":
      "Fedezd fel a térképet és a bal oldali Top 5 problémás járatot, hogy egy pillantással lásd a késéseket.",
    "guide.step.badge":
      "Az adatjelző (bal felül) mutatja, hogy valós idejű vagy teszt adatokat látsz.",

    "lines.title": "Helyi járatok",
    "lines.ariaList": "Helyi járatok",
    "lines.ariaAvailable": "Elérhető járatok",
    "lines.loading": "Helyi járatok betöltése…",
    "lines.error": "A helyi járatok jelenleg nem elérhetők.",
    "lines.empty": "Nincs találat helyi járatra.",

    "problematic.title": "Top 5 problémás járat",
    "problematic.loading": "Problémás járatok betöltése…",
    "problematic.error": "A problémás járatok jelenleg nem elérhetők.",
    "problematic.empty": "Jelenleg nincs késő járat.",

    "route.lineLabel": "Járat",
    "route.searchLines": "Járatok keresése…",
    "route.selectedLine": "Kiválasztott járat:",
    "route.loadingLines": "Járatok betöltése…",
    "route.linesError": "Nem sikerült betölteni a járatokat.",
    "route.flip": "Menetirány megfordítása",
    "route.flipDisabled": "Ennek a járatnak egy iránya van",
    "route.stopLabel": "Megálló",
    "route.searchStops": "Megállók keresése…",
    "route.selectStopFirst": "Előbb válassz járatot",
    "route.selectedStop": "Kiválasztott megálló:",
    "route.loadingStops": "Megállók betöltése…",
    "route.stopsError": "Nem sikerült betölteni a megállókat.",
    "route.noStops": "Nincs találat megállóra.",
    "route.noLines": "Nincs találat járatra.",
    "route.linesAvailable": "Elérhető járatok",
    "route.stopsList": "Járat megállói",

    "destination.label": "Célállomás",
    "destination.disabled": "Előbb válassz járatot és megállót",
    "destination.search": "Célállomások keresése…",
    "destination.ariaList": "Lejjebbi célállomások",
    "destination.selected": "Célállomás:",
    "destination.none": "Nincs lejjebbi megálló.",

    "departures.title": "Indulások",
    "departures.aria": "Közelgő indulások",
    "departures.prompt": "Válassz járatot és megállót az indulásokhoz.",
    "departures.loading": "Indulások betöltése…",
    "departures.error": "Az indulások jelenleg nem elérhetők.",
    "departures.empty": "Nincs közelgő indulás.",
    "departures.onTime": "időben",

    "prediction.title": "Előrejelzés",
    "prediction.prompt": "Válassz járatot és megállót az előrejelzéshez.",

    "nextride.title": "Következő járat",
    "nextride.departs": "Következő járat indul",
    "nextride.prompt": "Válassz járatot és megállót a következő járathoz.",
    "nextride.loading": "Következő járat betöltése…",
    "nextride.error": "A következő járat jelenleg nem elérhető.",
    "nextride.empty": "Nincs közelgő indulás.",

    "journey.title": "Útidő becslés",
    "journey.label": "Várható menetidő",
    "journey.prompt": "Válassz célállomást a várható menetidőhöz.",
    "journey.loading": "Útidő becslés betöltése…",
    "journey.error": "Az útidő becslés jelenleg nem elérhető.",
    "journey.notOnTrip": "Ez a célállomás nincs ezen a járaton.",

    "reliability.title": "Megbízhatóság",
    "reliability.loading": "Megbízhatóság betöltése…",
    "reliability.error": "Az előrejelzés jelenleg nem elérhető.",
    "reliability.unavailable": "Előrejelzés nem elérhető.",
    "reliability.weatherMissing":
      "Időjárási adat nem elérhető — a megbízhatóság csak közlekedési adatokon alapul.",

    "timeline.title": "Érkezési idővonal",
    "timeline.prompt": "Válassz járatot és megállót a várható érkezésekhez.",
    "timeline.loading": "Idővonal betöltése…",
    "timeline.error": "Az érkezési idővonal jelenleg nem elérhető.",
    "timeline.empty": "Nincs közelgő érkezés ehhez a megállóhoz.",
    "timeline.currentStop": "Jelenlegi megálló",

    "mode.bus": "Busz",
    "mode.tram": "Villamos",
    "mode.trolleybus": "Trolibusz",
    "mode.metro": "Metró",

    "pt.due": "most",
    "pt.in1": "1 perc múlva",
    "pt.inN": "{n} perc múlva",
    "pt.min": "{n} perc",
    "pt.delay.onTimeVerdict": "Nagyjából időben közlekedik",
    "pt.delay.behind": "~{n} perc késésben",
    "pt.delay.ahead": "~{n} perccel előbb jár",
    "pt.adj.asScheduled": "Nagyjából a menetrend szerint",
    "pt.adj.slower": "~{n} perccel lassabb a menetrendnél",
    "pt.adj.faster": "~{n} perccel gyorsabb a menetrendnél",
    "pt.short.onTime": "időben",
    "pt.short.late": "{n} perc késés",
    "pt.short.early": "{n} perc korábban",
    "pt.metric.late": "{n} perc késés",
  },
};

/** A translator bound to a language. */
export type Translate = (key: string, vars?: Record<string, string | number>) => string;

/** Build a translator for a language with English/key fallback + interpolation. */
export function makeTranslate(lang: Lang): Translate {
  return (key, vars) => {
    const template =
      MESSAGES[lang][key] ?? MESSAGES.en[key] ?? key;
    if (!vars) return template;
    return template.replace(/\{(\w+)\}/g, (_m, name) =>
      name in vars ? String(vars[name]) : `{${name}}`,
    );
  };
}

interface LanguageContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  toggle: () => void;
  t: Translate;
}

const LanguageContext = createContext<LanguageContextValue | null>(null);

function readInitialLang(): Lang {
  if (typeof window === "undefined") return "en";
  const stored = window.localStorage?.getItem(STORAGE_KEY);
  if (stored === "en" || stored === "hu") return stored;
  // Default to Hungarian if the browser prefers it, else English.
  const nav = window.navigator?.language?.toLowerCase() ?? "";
  return nav.startsWith("hu") ? "hu" : "en";
}

export interface LanguageProviderProps {
  children: ReactNode;
  /** Force an initial language (primarily for tests). */
  initialLang?: Lang;
}

export function LanguageProvider({
  children,
  initialLang,
}: LanguageProviderProps) {
  const [lang, setLangState] = useState<Lang>(
    () => initialLang ?? readInitialLang(),
  );

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      window.localStorage?.setItem(STORAGE_KEY, next);
    } catch {
      // Ignore storage errors (private mode, quota) — language still applies.
    }
  }, []);

  const toggle = useCallback(() => {
    setLang(lang === "en" ? "hu" : "en");
  }, [lang, setLang]);

  // Keep the document language in sync for accessibility / correct hyphenation.
  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.lang = lang;
    }
  }, [lang]);

  const value = useMemo<LanguageContextValue>(
    () => ({ lang, setLang, toggle, t: makeTranslate(lang) }),
    [lang, setLang, toggle],
  );

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
}

/**
 * Access the active language and translator. Falls back to a standalone English
 * translator when used outside a provider, so a component never crashes (and
 * unit tests can render it without wrapping).
 */
export function useLanguage(): LanguageContextValue {
  const ctx = useContext(LanguageContext);
  if (ctx != null) return ctx;
  return {
    lang: "en",
    setLang: () => {},
    toggle: () => {},
    t: makeTranslate("en"),
  };
}

/** Convenience hook returning just the translator. */
export function useT(): Translate {
  return useLanguage().t;
}
