// Interface translation, hand-rolled rather than react-i18next.
//
// Why not the library: this bundle ships inside a 58 MB .dmg and builds offline, and what
// the UI actually needs is lookup, interpolation, plurals and locale-aware number/date
// formatting. `Intl` covers the last three natively, so a dependency would add weight and
// an upstream-merge conflict surface to save maybe forty lines.
//
// English is the source language: `en.ts` mirrors the strings as they appear in the JSX,
// and a key missing from another catalogue falls back to it rather than rendering blank.

import { en } from "./en";
import { ptBR } from "./pt-BR";

export type Locale = "en" | "pt-BR";
export const LOCALES: Locale[] = ["en", "pt-BR"];
export const DEFAULT_LOCALE: Locale = "en";

/** Native names, so the picker reads in the language it selects. */
export const LOCALE_NAMES: Record<Locale, string> = {
  en: "English",
  "pt-BR": "Português (Brasil)",
};

// A catalogue value is either one string, or the plural forms for a count. `other` is the
// only required form: English needs one/other, pt-BR the same, and a language needing more
// (Polish, Arabic) adds its forms here without touching call sites.
export type Plural = { one?: string; other: string; zero?: string };
export type Message = string | Plural;
export type Catalog = Record<string, Message>;

const CATALOGS: Record<Locale, Catalog> = { en, "pt-BR": ptBR };

export function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as string[]).includes(value);
}

/** Replace `{name}` placeholders. Unknown placeholders are left verbatim, which shows up
 * as `{oops}` on screen — visible in review, rather than silently dropping content. */
function interpolate(text: string, vars?: Record<string, string | number>): string {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (whole, key: string) =>
    key in vars ? String(vars[key]) : whole,
  );
}

function pluralForm(message: Plural, count: number, locale: Locale): string {
  if (count === 0 && message.zero !== undefined) return message.zero;
  // Intl knows each language's rules; pt-BR treats 0 as plural where English says "0 items"
  // too, but the categories differ elsewhere and hardcoding `n === 1` gets those wrong.
  const category = new Intl.PluralRules(locale).select(count);
  if (category === "one" && message.one !== undefined) return message.one;
  return message.other;
}

/** Look a key up, falling back to English and finally to the key itself.
 *
 * Returning the key (rather than empty) when nothing matches means an untranslated string
 * is obvious on screen instead of leaving a hole in the layout. */
export function translate(
  locale: Locale,
  key: string,
  vars?: Record<string, string | number>,
): string {
  const message = CATALOGS[locale]?.[key] ?? CATALOGS[DEFAULT_LOCALE][key];
  if (message === undefined) return key;
  const text =
    typeof message === "string"
      ? message
      : pluralForm(message, Number(vars?.count ?? 0), locale);
  return interpolate(text, vars);
}

// -- locale-aware formatting ----------------------------------------------------
// The codebase had none of this: `toFixed()` everywhere (always a `.` decimal separator,
// wrong for pt-BR) and relative times assembled from English fragments like `${m}m ago`.

export function formatNumber(
  locale: Locale,
  value: number,
  options?: Intl.NumberFormatOptions,
): string {
  return new Intl.NumberFormat(locale, options).format(value);
}

/** A count of bytes as a short human string ("1,2 MB" in pt-BR, "1.2 MB" in en). */
export function formatBytes(locale: Locale, bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = Math.max(0, bytes);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1;
  return `${formatNumber(locale, value, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} ${units[unit]}`;
}

/** "5 min atrás" / "5 min ago", from a unix timestamp in seconds.
 *
 * Uses Intl.RelativeTimeFormat so the unit, the number and the word order all come from
 * the locale — the previous `${mins}m ago` template can't be translated without rewriting
 * it per language. */
export function formatRelativeTime(locale: Locale, epochSeconds: number): string {
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto", style: "narrow" });
  const seconds = Math.round(epochSeconds - Date.now() / 1000);
  const abs = Math.abs(seconds);
  const steps: [Intl.RelativeTimeFormatUnit, number][] = [
    ["second", 60],
    ["minute", 3600],
    ["hour", 86400],
    ["day", 2592000],
    ["month", 31536000],
  ];
  let unit: Intl.RelativeTimeFormatUnit = "year";
  let divisor = 31536000;
  let previous = 1;
  for (const [candidate, limit] of steps) {
    if (abs < limit) {
      unit = candidate;
      divisor = previous;
      break;
    }
    previous = limit;
  }
  if (unit === "year") divisor = 31536000;
  return rtf.format(Math.round(seconds / divisor), unit);
}

export function formatDateTime(
  locale: Locale,
  value: Date | string | number,
  options: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" },
): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(locale, options).format(date);
}
