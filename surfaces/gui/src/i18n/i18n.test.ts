import { describe, expect, it } from "vitest";
import {
  formatBytes,
  formatDateTime,
  formatNumber,
  formatRelativeTime,
  isLocale,
  translate,
} from "./index";

const secondsFromNow = (offset: number) => Date.now() / 1000 + offset;

describe("translate", () => {
  it("returns the catalogue string for the locale", () => {
    expect(translate("en", "common.cancel")).toBe("Cancel");
    expect(translate("pt-BR", "common.cancel")).toBe("Cancelar");
  });

  it("falls back to English for a key the locale hasn't translated", () => {
    // pt-BR intentionally leaves provider terms in English; a key it doesn't define at all
    // must still render rather than leaving a hole in the layout.
    expect(translate("pt-BR", "byo.appId")).toBe("App ID");
  });

  it("returns the key itself when nothing matches", () => {
    // Visible on screen, so an untranslated string is obvious in review.
    expect(translate("pt-BR", "no.such.key")).toBe("no.such.key");
  });

  it("interpolates variables", () => {
    expect(translate("pt-BR", "byo.connectTitle", { title: "Notion" })).toBe(
      "Conectar Notion",
    );
  });

  it("leaves an unknown placeholder verbatim rather than dropping it", () => {
    // `{title}` surviving on screen is a bug you can see; silently blank is one you can't.
    expect(translate("en", "byo.connectTitle")).toBe("Connect {title}");
  });
});

describe("plurals", () => {
  it("selects English forms by count", () => {
    expect(translate("en", "count.repositories", { count: 1 })).toBe("1 repository");
    expect(translate("en", "count.repositories", { count: 2 })).toBe("2 repositories");
  });

  it("selects pt-BR forms by count", () => {
    expect(translate("pt-BR", "count.repositories", { count: 1 })).toBe("1 repositório");
    expect(translate("pt-BR", "count.repositories", { count: 3 })).toBe("3 repositórios");
  });

  it("uses the explicit zero form in pt-BR", () => {
    // CLDR classifies 0 as `one` for pt-BR, so relying on Intl alone yields the singular
    // "0 repositório". Brazilian usage is plural, hence the explicit `zero` entry.
    expect(new Intl.PluralRules("pt-BR").select(0)).toBe("one");
    expect(translate("pt-BR", "count.repositories", { count: 0 })).toBe(
      "nenhum repositório",
    );
  });

  it("keeps English zero on the plural form", () => {
    expect(translate("en", "count.repositories", { count: 0 })).toBe("0 repositories");
  });
});

describe("number formatting", () => {
  it("uses the locale's decimal and grouping separators", () => {
    // The pre-existing code used toFixed(), which always emits a `.` — wrong for pt-BR.
    expect(formatNumber("en", 1234.5)).toBe("1,234.5");
    expect(formatNumber("pt-BR", 1234.5)).toBe("1.234,5");
  });

  it("formats byte sizes in the locale", () => {
    expect(formatBytes("en", 1536000)).toBe("1.5 MB");
    expect(formatBytes("pt-BR", 1536000)).toBe("1,5 MB");
  });

  it("keeps small byte counts whole", () => {
    expect(formatBytes("en", 512)).toBe("512 B");
    expect(formatBytes("en", 0)).toBe("0 B");
  });
});

describe("relative time", () => {
  it("picks the right unit per magnitude", () => {
    expect(formatRelativeTime("en", secondsFromNow(-30))).toContain("30");
    expect(formatRelativeTime("en", secondsFromNow(-300))).toBe("5m ago");
    expect(formatRelativeTime("en", secondsFromNow(-7200))).toBe("2h ago");
    expect(formatRelativeTime("en", secondsFromNow(-259200))).toBe("3d ago");
  });

  it("translates the unit and word order, not just the number", () => {
    // The previous `${mins}m ago` template couldn't be translated without rewriting it per
    // language; Intl puts "há" in front for pt-BR on its own.
    expect(formatRelativeTime("pt-BR", secondsFromNow(-300))).toBe("há 5 min.");
    expect(formatRelativeTime("pt-BR", secondsFromNow(-259200))).toBe("há 3 dias");
  });

  it("handles months and years, and the future", () => {
    expect(formatRelativeTime("pt-BR", secondsFromNow(-5184000))).toBe("há 2 meses");
    expect(formatRelativeTime("pt-BR", secondsFromNow(-94608000))).toBe("há 3 anos");
    expect(formatRelativeTime("en", secondsFromNow(300))).toBe("in 5m");
  });
});

describe("dates", () => {
  it("formats in the locale's conventions", () => {
    const when = new Date("2026-03-09T15:30:00Z");
    // Day-first in pt-BR, month-first in en — asserted loosely so a CI timezone doesn't
    // make this brittle.
    expect(formatDateTime("pt-BR", when, { dateStyle: "short" })).toMatch(/^0?9\/0?3/);
    expect(formatDateTime("en", when, { dateStyle: "short" })).toMatch(/^0?3\/0?9/);
  });

  it("returns empty for an unparseable date rather than 'Invalid Date'", () => {
    expect(formatDateTime("en", "not a date")).toBe("");
  });
});

describe("isLocale", () => {
  it("accepts the shipped locales and rejects anything else", () => {
    expect(isLocale("pt-BR")).toBe(true);
    expect(isLocale("en")).toBe(true);
    expect(isLocale("pt")).toBe(false);
    expect(isLocale(undefined)).toBe(false);
  });
});
