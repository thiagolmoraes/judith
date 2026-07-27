import { describe, expect, it } from "vitest";
import { humanizeApprovalTitle, humanizeAsk, humanizeTool } from "../humanize";
import { en } from "./en";
import { ptBR } from "./pt-BR";
import { translate } from "./index";

const pt = (key: string, vars?: Record<string, string | number>) =>
  translate("pt-BR", key, vars);

describe("catalogue integrity", () => {
  it("pt-BR covers every English key", () => {
    // English is the fallback, so a missing key ships silently in English rather than
    // breaking. This is what makes that visible.
    const missing = Object.keys(en).filter((k) => !(k in ptBR));
    expect(missing, `untranslated keys: ${missing.join(", ")}`).toEqual([]);
  });

  it("has no pt-BR entries without an English source", () => {
    // A key with no English counterpart is dead weight, and usually a rename that left
    // the live string untranslated.
    const stale = Object.keys(ptBR).filter((k) => !(k in en));
    expect(stale, `stale keys: ${stale.join(", ")}`).toEqual([]);
  });

  it("keeps placeholders identical across languages", () => {
    // A translation that drops {name} renders a literal brace or loses the value.
    const names = (v: unknown): Set<string> => {
      const text = typeof v === "string" ? v : Object.values(v as object).join(" ");
      return new Set([...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]));
    };
    for (const key of Object.keys(en)) {
      if (!(key in ptBR)) continue;
      expect([...names(en[key])].sort(), key).toEqual([...names(ptBR[key])].sort());
    }
  });
});

describe("tool-call one-liners", () => {
  it("defaults to English so existing call sites are unchanged", () => {
    expect(humanizeTool("read_file", { path: "/a/runbook.md" })).toEqual({
      pre: "Read ",
      obj: "runbook.md",
    });
  });

  it("translates the verb while leaving the object alone", () => {
    // The object is a filename or a quoted user string — translating it would be wrong.
    expect(humanizeTool("read_file", { path: "/a/runbook.md" }, pt)).toEqual({
      pre: "Leu ",
      obj: "runbook.md",
    });
  });

  it("handles the fragments with no object", () => {
    expect(humanizeTool("git_log", {}, pt).pre).toBe(
      "Consultou o histórico recente do git",
    );
  });

  it("interpolates counts and tool names", () => {
    expect(humanizeTool("todo_write", { todos: [1, 2, 3] }, pt).pre).toBe(
      "Atualizou o plano — 3 itens",
    );
    expect(humanizeTool("some_mcp_tool", {}, pt).pre).toContain("Usou");
  });

  it("uses the infinitive for pending approvals, past tense for history", () => {
    // English leans on imperative vs past ("Run a command" / "Ran"); Portuguese uses the
    // infinitive for the ask, which is why these are separate keys rather than one.
    expect(humanizeApprovalTitle("run_shell", { command: "ls" }, pt).pre).toBe(
      "Executar um comando",
    );
    expect(humanizeAsk("run_shell", { command: "ls" }, pt).pre).toContain("Queria");
  });

  it("keeps the pre/obj/post shape so the UI can still bold the object", () => {
    const line = humanizeTool("grep", { pattern: "TODO" }, pt);
    expect(line.pre).toBe("Procurou no código por ");
    expect(line.obj).toContain("TODO");
  });
});
