import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { getSettings, setLocale as persistLocale } from "../api";
import {
  DEFAULT_LOCALE,
  formatBytes,
  formatDateTime,
  formatNumber,
  formatRelativeTime,
  isLocale,
  translate,
  type Locale,
} from "./index";

// Locale lives in the sidecar's prefs, not localStorage: the same choice has to apply to
// surfaces the browser doesn't own (the loopback OAuth pages, the TUI) and has to survive
// a reinstall of the desktop app.

export interface I18n {
  locale: Locale;
  /** Translate a key, interpolating `{vars}`. `count` also selects the plural form. */
  t: (key: string, vars?: Record<string, string | number>) => string;
  setLocale: (next: Locale) => Promise<void>;
  n: (value: number, options?: Intl.NumberFormatOptions) => string;
  bytes: (value: number) => string;
  relative: (epochSeconds: number) => string;
  date: (value: Date | string | number, options?: Intl.DateTimeFormatOptions) => string;
}

const I18nContext = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);

  useEffect(() => {
    let live = true;
    void getSettings()
      .then((s) => {
        // Unknown value (a catalogue that was removed) falls back rather than breaking.
        if (live && isLocale(s?.locale)) setLocaleState(s.locale);
      })
      .catch(() => {
        /* keep the default; the app must render even if settings can't be read */
      });
    return () => {
      live = false;
    };
  }, []);

  const setLocale = useCallback(async (next: Locale) => {
    // Optimistic: the UI switches immediately and the write is what persists it. A failed
    // write means the old language returns on next launch, which is recoverable — leaving
    // the user staring at an unchanged UI while a request flies is not.
    setLocaleState(next);
    try {
      await persistLocale(next);
    } catch {
      /* keep the switch; it just won't survive a restart */
    }
  }, []);

  const value = useMemo<I18n>(
    () => ({
      locale,
      t: (key, vars) => translate(locale, key, vars),
      setLocale,
      n: (v, options) => formatNumber(locale, v, options),
      bytes: (v) => formatBytes(locale, v),
      relative: (epoch) => formatRelativeTime(locale, epoch),
      date: (v, options) => formatDateTime(locale, v, options),
    }),
    [locale, setLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

/** Translation helpers for the current locale.
 *
 * Falls back to a working English instance when used outside the provider, so a component
 * rendered in isolation (a test, a storybook-style harness) doesn't crash — a missing
 * provider should not be able to take the app down. */
export function useI18n(): I18n {
  const ctx = useContext(I18nContext);
  return (
    ctx ?? {
      locale: DEFAULT_LOCALE,
      t: (key, vars) => translate(DEFAULT_LOCALE, key, vars),
      setLocale: async () => {},
      n: (v, options) => formatNumber(DEFAULT_LOCALE, v, options),
      bytes: (v) => formatBytes(DEFAULT_LOCALE, v),
      relative: (epoch) => formatRelativeTime(DEFAULT_LOCALE, epoch),
      date: (v, options) => formatDateTime(DEFAULT_LOCALE, v, options),
    }
  );
}
