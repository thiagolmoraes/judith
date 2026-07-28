import { describe, expect, it } from "vitest";
import { en } from "./en";
import { ptBR } from "./pt-BR";
import { translate } from "./index";

// The always-visible chrome: the composer, the top bar, the sidebar. Every previous pass over
// these screens shipped with strings still in English, because each sweep I wrote was narrower
// than the thing it was guarding — first tag text only, then template literals, then
// interpolated sentences. This file is the widest form of that sweep, kept as a test so the
// next edit can't quietly reintroduce a literal.

const FILES = import.meta.glob("../{App,components/Composer,components/Sidebar}.tsx", {
  query: "?raw",
  import: "default",
  eager: false,
});

/** Source with comments and imports stripped — prose about a string is not a string. */
function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1 ")
    .replace(/^import[\s\S]*?from\s*["'][^"']+["'];?$/gm, " ");
}

// Identifiers, CSS, ids and glyphs all look like strings to a regex. These are the shapes that
// are legitimately not user-facing.
const NOT_PROSE = [
  /^[a-z][a-zA-Z0-9]*$/, // camelCase identifier
  /^[a-z0-9-]+(?:[ ][a-z0-9-]+)*$/, // css classes / kebab ids, lowercase throughout
  /^[A-Z][a-zA-Z0-9]*$/, // a single capitalised word with no space: a type or enum value
  /^[\W\d\s]+$/, // punctuation, digits, emoji
  /^\p{Extended_Pictographic}/u,
];

const isProse = (s: string) =>
  s.trim().length > 3 && /[a-z]\s+[a-z]/i.test(s) && !NOT_PROSE.some((r) => r.test(s.trim()));

describe("the core shell has no untranslated user-facing text", () => {
  it("has no English sentences left in JSX attributes", async () => {
    // title / aria-label / placeholder are where the earlier passes leaked most: they read as
    // configuration rather than copy, so they get skipped by eye.
    const offenders: string[] = [];
    for (const [path, load] of Object.entries(FILES)) {
      const text = code((await load()) as string);
      for (const m of text.matchAll(
        /(?:title|placeholder|aria-label|ariaLabel|alt)=\{?"([^"]+)"/g,
      )) {
        if (isProse(m[1])) offenders.push(`${path}: ${m[1]}`);
      }
    }
    expect(offenders, `hardcoded attribute copy:\n${offenders.join("\n")}`).toEqual([]);
  });

  it("has no English sentences left as JSX text", async () => {
    const offenders: string[] = [];
    for (const [path, load] of Object.entries(FILES)) {
      // Whitespace-collapsed: JSX wraps a sentence across lines, and a line-oriented search
      // misses exactly the long strings most worth catching.
      const text = code((await load()) as string).replace(/\s+/g, " ");
      for (const m of text.matchAll(/>\s*([A-Z][^<>{}]{4,})\s*</g)) {
        if (isProse(m[1])) offenders.push(`${path}: ${m[1].trim()}`);
      }
    }
    expect(offenders, `hardcoded JSX copy:\n${offenders.join("\n")}`).toEqual([]);
  });

  it("builds no sentence with an inline English plural rule", async () => {
    // `run${n === 1 ? "" : "s"}` is a rule that only holds in English, and it also gets pt-BR's
    // zero wrong. Counts belong in the catalogue as plural entries.
    const offenders: string[] = [];
    for (const [path, load] of Object.entries(FILES)) {
      const text = code((await load()) as string).replace(/\s+/g, " ");
      for (const m of text.matchAll(/\$\{[^}]*\?\s*""\s*:\s*"s"[^}]*\}/g)) {
        offenders.push(`${path}: ${m[0]}`);
      }
      for (const m of text.matchAll(/\$\{[^}]*\?\s*"s"\s*:\s*""[^}]*\}/g)) {
        offenders.push(`${path}: ${m[0]}`);
      }
    }
    expect(offenders, `inline plural rules:\n${offenders.join("\n")}`).toEqual([]);
  });
});

describe("the keys these screens use", () => {
  it("translates the composer's mode menu into whole options", () => {
    // Label and description are separate keys but render as one row; a half-translated row
    // reads worse than an untranslated one.
    for (const key of [
      "composer.discuss",
      "composer.discussHint",
      "composer.askApproval",
      "composer.askBeforeEdits",
      "composer.fullAccess",
      "composer.runEverything",
    ]) {
      expect(ptBR[key], key).toBeDefined();
      expect(translate("pt-BR", key)).not.toBe(en[key]);
    }
  });

  it("pluralises the sidebar's unseen-run badge with a zero form", () => {
    expect(translate("pt-BR", "count.newRuns", { count: 0 })).toBe("nenhuma execução nova");
    expect(translate("pt-BR", "count.newRuns", { count: 1 })).toBe("1 execução nova");
    expect(translate("pt-BR", "count.newRuns", { count: 5 })).toBe("5 execuções novas");
  });

  it("keeps the scheduled-run banner a single sentence", () => {
    // It used to be three JSX fragments joined by " — " and " · ", which can only be
    // reassembled in English word order.
    const named = translate("pt-BR", "app.scheduledRunNamed", { title: "Relatório" });
    expect(named).toContain("Relatório");
    expect(named).not.toContain("Scheduled");
    expect(translate("pt-BR", "app.scheduledRunPlain")).not.toContain("Scheduled");
  });
});
