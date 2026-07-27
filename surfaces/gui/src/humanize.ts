// UX-015 (§33): tool calls render as English one-liners. The model does NOT emit a purpose
// per call — the stream is name+args+result — so the sentence is synthesized here from
// per-tool templates. `run_shell` is the exception: its optional `description` argument is
// model-written intent and is preferred when present. Fallback: "Used <tool> — <short args>".

import { shortArgs } from "./components/ApprovalCard";
import { translate } from "./i18n";

// A one-line sentence in three segments so the UI can emphasize the object:
// "Read " + <b>runbook.md</b> + " from the shared folder".
export interface HumanLine {
  pre: string;
  obj?: string;
  post?: string;
}

/** The translator these builders format through.
 *
 * Passed in rather than imported: this module is pure and is called from a plain function
 * in Transcript's render path, where a React hook isn't available. Defaulting to English
 * keeps every existing call site working untouched. */
export type Translate = (key: string, vars?: Record<string, string | number>) => string;

/** English, from the shared catalogue — so these fragments live in one place with the
 * rest of the UI copy rather than being duplicated here. */
const EN: Translate = (key, vars) => translate("en", key, vars);

const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const baseName = (p: string) => p.replace(/\/+$/, "").split("/").pop() || p;

// send_message targets are "platform:chat" or "platform:chat:thread" — show the platform
// by name and the last human-ish segment of the chat id.
function messageTarget(target: string): { platform: string; tail: string } {
  const [platform, ...rest] = String(target).split(":");
  const chat = rest[0] || "";
  const tail = chat.includes("/") ? chat.split("/").pop() || chat : chat;
  const names: Record<string, string> = { slack: "Slack", telegram: "Telegram" };
  return { platform: names[platform] || platform, tail };
}

export function humanizeTool(name: string, args: any, t: Translate = EN): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "run_shell": {
      const cmd = trunc(String(a.command ?? ""), 60);
      const desc = typeof a.description === "string" && a.description.trim() ? a.description.trim() : "";
      const pre = a.run_in_background
        ? t("tool.startedBackground")
        : t("tool.ranCommand");
      return {
        pre,
        obj: cmd,
        ...(desc ? { post: ` — ${desc.charAt(0).toLowerCase()}${desc.slice(1)}` } : {}),
      };
    }
    case "shell_task_output":
      return { pre: t("tool.shellTaskOutput") };
    case "shell_task_kill":
      return { pre: t("tool.shellTaskKill") };
    case "read_file":
      return { pre: t("tool.readFile"), obj: baseName(String(a.path ?? t("tool.aFile"))) };
    case "write_file":
      return { pre: t("tool.writeFile"), obj: baseName(String(a.path ?? t("tool.aFile"))) };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return { pre: t("tool.editFile"), obj: a.path ? baseName(String(a.path)) : t("tool.files") };
    case "grep":
      return { pre: t("tool.grep"), obj: `“${trunc(String(a.pattern ?? ""), 40)}”` };
    case "git_log":
      return { pre: t("tool.gitLog") };
    case "todo_write": {
      // `todos` is current; `items` renders histories from before the rename (the old
      // key breaks Together's GLM-5.2 chat template — see coworker/tools/todo.py).
      const items = Array.isArray(a.todos) ? a.todos : Array.isArray(a.items) ? a.items : [];
      if (items.length === 1) {
        const it = items[0] || {};
        // Known statuses go through the catalogue; anything else keeps the raw value
        // with underscores spaced, so a new status added server-side still renders.
        const raw = String(it.status || "");
        const statusKeys: Record<string, string> = {
          pending: "todo.status.pending",
          in_progress: "todo.status.inProgress",
          completed: "todo.status.completed",
        };
        const status = raw ? (statusKeys[raw] ? t(statusKeys[raw]) : raw.replace(/_/g, " ")) : "";
        return {
          pre: t("tool.updatedPlan"),
          obj: `“${trunc(String(it.content ?? ""), 70)}”`,
          ...(status ? { post: ` → ${status}` } : {}),
        };
      }
      return { pre: t("tool.updatedPlanItems", { count: items.length }) };
    }
    case "send_message": {
      const { platform, tail } = messageTarget(String(a.target ?? ""));
      if (!tail) return { pre: t("tool.sentMessage") };
      return { pre: t("tool.sentPlatformMessageTo", { platform }), obj: tail };
    }
    case "web_search":
      return { pre: t("tool.searchedWeb"), obj: `“${trunc(String(a.query ?? ""), 60)}”` };
    case "web_fetch": {
      let host = String(a.url ?? "");
      try {
        host = new URL(host).host || host;
      } catch {
        /* keep raw */
      }
      return { pre: t("tool.readWebPage"), obj: trunc(host, 50) };
    }
    case "explore":
      return { pre: t("tool.subagent"), obj: `“${trunc(String(a.task ?? a.prompt ?? ""), 60)}”` };
    case "ask_user":
      return { pre: t("tool.askedQuestion") };
    case "propose_plan":
      return { pre: t("tool.proposedPlan") };
    case "request_directory":
      return { pre: t("tool.askedFolderAccess"), obj: String(a.path ?? "") };
    default: {
      const rest = trunc(shortArgs(a), 80);
      return { pre: t("tool.used", { name }), ...(rest ? { post: ` — ${rest}` } : {}) };
    }
  }
}

// The approval card's headline (§35): the ask, phrased as the action being decided.
// run_shell leads with the model's own description ("Run a command — fetch stock data").
export function humanizeApprovalTitle(name: string, args: any, t: Translate = EN): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "write_file":
      return {
        pre: t("tool.pending.write"),
        obj: baseName(String(a.path ?? t("tool.aFile"))),
      };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return {
        pre: t("tool.pending.edit"),
        obj: a.path ? baseName(String(a.path)) : t("tool.files"),
      };
    case "run_shell": {
      const desc = typeof a.description === "string" && a.description.trim() ? a.description.trim() : "";
      return {
        pre: t("tool.pending.run"),
        ...(desc ? { post: ` — ${desc.charAt(0).toLowerCase()}${desc.slice(1)}` } : {}),
      };
    }
    case "send_message": {
      const { tail } = messageTarget(String(a.target ?? ""));
      return tail
        ? { pre: t("tool.pending.sendMessageTo"), obj: tail }
        : { pre: t("tool.pending.sendMessage") };
    }
    case "send_file": {
      const { tail } = messageTarget(String(a.target ?? ""));
      return tail
        ? { pre: t("tool.pending.sendFileTo"), obj: tail }
        : { pre: t("tool.pending.sendFile") };
    }
    case "create_scheduled_task":
      return a.title
        ? { pre: t("tool.pending.createAutomationNamed"), obj: `“${trunc(String(a.title), 60)}”` }
        : { pre: t("tool.pending.createAutomation") };
    default:
      return { pre: t("tool.pending.use", { name }) };
  }
}

// Approvals with no executed tool call (typically declined): the ask, phrased as intent.
export function humanizeAsk(name: string, args: any, t: Translate = EN): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "run_shell":
      return { pre: t("tool.wanted.run"), obj: trunc(String(a.command ?? ""), 60) };
    case "write_file":
      return {
        pre: t("tool.wanted.write"),
        obj: baseName(String(a.path ?? t("tool.aFile"))),
      };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return {
        pre: t("tool.wanted.edit"),
        obj: a.path ? baseName(String(a.path)) : t("tool.files"),
      };
    case "send_message": {
      const { platform, tail } = messageTarget(String(a.target ?? ""));
      if (!tail) return { pre: t("tool.wanted.sendMessage") };
      return { pre: t("tool.wanted.message"), obj: tail, post: t("tool.onPlatform", { platform }) };
    }
    default:
      return { pre: t("tool.wanted.use", { name }) };
  }
}
