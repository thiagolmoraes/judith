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
    // Assert the interpolated name, not just the verb: `Usou {name}` with a dropped
    // placeholder would still contain "Usou".
    expect(humanizeTool("some_mcp_tool", {}, pt).pre).toBe("Usou some_mcp_tool");
  });

  it("translates run_shell, the most common line of all", () => {
    // This one shipped untranslated in the first pass and the suite didn't notice,
    // because it tested every path except the one users see most.
    expect(humanizeTool("run_shell", { command: "ls -la" }, pt)).toMatchObject({
      pre: "Executou ",
      obj: "ls -la",
    });
    expect(
      humanizeTool("run_shell", { command: "npm test", run_in_background: true }, pt).pre,
    ).toBe("Iniciou em segundo plano: ");
  });

  it("translates todo statuses rather than passing them through", () => {
    const line = humanizeTool(
      "todo_write",
      { todos: [{ content: "ship it", status: "in_progress" }] },
      pt,
    );
    expect(line.post).toBe(" → em andamento");
  });

  it("keeps an unknown todo status readable instead of dropping it", () => {
    // A status added server-side must still render — spaced, not underscored.
    const line = humanizeTool(
      "todo_write",
      { todos: [{ content: "x", status: "waiting_on_review" }] },
      pt,
    );
    expect(line.post).toBe(" → waiting on review");
  });

  it("translates the no-path fallbacks in both approval forms", () => {
    // `tool.aFile` / `tool.files` existed but four call sites still used the English
    // literals, so a pending edit read "Editar files" in a Portuguese UI.
    expect(humanizeApprovalTitle("write_file", {}, pt).obj).toBe("um arquivo");
    expect(humanizeApprovalTitle("apply_patch", {}, pt).obj).toBe("arquivos");
    expect(humanizeAsk("write_file", {}, pt).obj).toBe("um arquivo");
    expect(humanizeAsk("apply_patch", {}, pt).obj).toBe("arquivos");
  });

  it("uses the infinitive for pending approvals, past tense for history", () => {
    // English leans on imperative vs past ("Run a command" / "Ran"); Portuguese uses the
    // infinitive for the ask, which is why these are separate keys rather than one.
    expect(humanizeApprovalTitle("run_shell", { command: "ls" }, pt).pre).toBe(
      "Executar um comando",
    );
    expect(humanizeAsk("run_shell", { command: "ls" }, pt).pre).toContain("Queria");
  });

  it("reads as a whole sentence once assembled, not just per fragment", () => {
    // Fragments can each look right and still join badly — "Queria enviar mensagem eng"
    // was missing both the article and the preposition while every key was "translated".
    const join = (l: { pre: string; obj?: string; post?: string }) =>
      `${l.pre}${l.obj ?? ""}${l.post ?? ""}`;

    expect(join(humanizeAsk("send_message", { target: "slack:eng" }, pt))).toBe(
      "Queria enviar uma mensagem para eng no Slack",
    );
    expect(join(humanizeTool("send_message", { target: "slack:eng" }, pt))).toBe(
      "Enviou uma mensagem no Slack para eng",
    );
    expect(join(humanizeApprovalTitle("send_file", { target: "slack:eng" }, pt))).toBe(
      "Enviar um arquivo para eng",
    );
    expect(join(humanizeTool("read_file", { path: "/a/b.md" }, pt))).toBe("Leu b.md");
  });

  it("keeps the pre/obj/post shape so the UI can still bold the object", () => {
    const line = humanizeTool("grep", { pattern: "TODO" }, pt);
    expect(line.pre).toBe("Procurou no código por ");
    expect(line.obj).toContain("TODO");
  });
});
