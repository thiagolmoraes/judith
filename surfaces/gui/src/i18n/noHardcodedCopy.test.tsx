import { describe, expect, it } from "vitest";
import { en } from "./en";
import { ptBR } from "./pt-BR";
import { translate } from "./index";

// The GUI is fully translated, and this keeps it that way. Every previous pass shipped with
// strings still in English, because each sweep I wrote was narrower than the thing it was
// guarding — first tag text only, then template literals, then interpolated sentences, then a
// plain function returning a string. This file is the widest form of that sweep, kept as a test.
//
// It covers ALL of src/ rather than a list of translated files. A list has the same flaw as the
// shape-based exemptions this guard used to have: a new screen is born outside it and nobody
// notices. Opting out is possible but has to be deliberate and explained.

const EXEMPT = [
  // Most of this file draws a FICTIONAL Slack workspace to teach how mentions arrive. It
  // imitates Slack's own interface, so a Portuguese rendering would depict a product that
  // doesn't exist. Its own copy — heading, tabs, captions, sticky notes — IS translated.
  "SlackHowItWorks.tsx",
];

const ALL = import.meta.glob("../**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: false,
});
const FILES = Object.fromEntries(
  Object.entries(ALL).filter(
    ([path]) =>
      !path.includes(".test.") && !EXEMPT.some((name) => path.endsWith(name)),
  ),
);

/** Source with comments and imports stripped — prose about a string is not a string. */
function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1 ")
    .replace(/^import[\s\S]*?from\s*["'][^"']+["'];?$/gm, " ")
    // `=> Promise<void>` reads as JSX text to the >…< scan below. Generic type arguments
    // are not copy, so drop them before scanning.
    .replace(/=>\s*[A-Z]\w*</g, "=> <");
}

// Exempting by SHAPE was the bug in the first version of this guard: "a single capitalised word
// is probably an enum value" also exempts `Delete`, `Send` and `Search`, which are button labels.
// Two of those were live in the sidebar while this test was green.
//
// So the exemptions are an explicit allowlist instead. A new one has to be added deliberately,
// which is the point — the cost of a false positive is one line here, the cost of a false
// negative is untranslated copy shipping unnoticed.
const ALLOWED = new Set([
  "OpenWorker", // the wordmark
  "BETA",
  "Coworker", // product surface names, also persona ids
  "Chat",
  "Code",
  "PDF",
  "Granola", // vendor names
  "Slack",
  "GitHub",
  "Notion",
  "HubSpot",
  "Attio",
  "Outlook",
  "Gmail",
  "Google Calendar", // the product's own name, on its badge
  // System requirements: OS names and version strings are the same in every language,
  // and "macOS 12+ · Apple Silicon M1+" is not a sentence to translate.
  "Mac",
  "Windows",
  "macOS 12+ · Apple Silicon M1+",
  "Windows 10 22H2/11 · x64",
  "~/OpenWorker", // a filesystem path shown as a placeholder
]);

// Shapes that can't be user-facing copy no matter what they say.
const LOWERCASE_WORDS = /^[a-z0-9-]+(?:[ ][a-z0-9-]+)*$/;
const NOT_COPY = [
  /^[a-z][a-zA-Z0-9]*$/, // camelCase identifier
  LOWERCASE_WORDS, // css classes / kebab ids, lowercase throughout
  /^[\W\d\s]+$/, // punctuation, digits, emoji
  /^\p{Extended_Pictographic}/u,
  /[;{}()=]|=>/, // a fragment of code the regex tore out of context
];

/** Does this literal read as something a person would see?
 *
 * `inAttribute` drops the lowercase-words exemption. In JSX text `foo bar` is nearly always a
 * className the regex caught mid-expression, but in `aria-label="new session"` it is the
 * accessible name — the one place lowercase prose is real copy. */
const isCopy = (raw: string, inAttribute = false) => {
  const s = raw.trim();
  // Two chars, not four: `Go`, `OK` and `Up` are all real button labels.
  if (s.length < 2 || ALLOWED.has(s)) return false;
  if (!/[A-Za-z]/.test(s)) return false;
  const shapes = inAttribute
    ? NOT_COPY.filter((r) => r.source !== LOWERCASE_WORDS.source)
    : NOT_COPY;
  return !shapes.some((r) => r.test(s));
};

describe("translated screens have no untranslated user-facing text", () => {
  it("has no English sentences left in JSX attributes", async () => {
    // title / aria-label / placeholder are where the earlier passes leaked most: they read as
    // configuration rather than copy, so they get skipped by eye.
    const offenders: string[] = [];
    for (const [path, load] of Object.entries(FILES)) {
      const text = code((await load()) as string);
      for (const m of text.matchAll(
        /(?:title|placeholder|aria-label|ariaLabel|alt)=\{?"([^"]+)"/g,
      )) {
        if (isCopy(m[1], true)) offenders.push(`${path}: ${m[1]}`);
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
      // {1,} not {4,}: a short label is exactly what the shape-based exemption used to hide.
      for (const m of text.matchAll(/>\s*([A-Za-z][^<>{}]{1,})\s*</g)) {
        if (isCopy(m[1])) offenders.push(`${path}: ${m[1].trim()}`);
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
      // Any interpolation that picks between two string literals. The first version only
      // matched the ""/"s" suffix form, so `${n === 1 ? "run" : "runs"}` — the same rule
      // spelled out in full — sailed past it.
      for (const m of text.matchAll(/\$\{[^}]*\?\s*"[^"]*"\s*:\s*"[^"]*"[^}]*\}/g)) {
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
