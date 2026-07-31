import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ApprovalsPanel } from "./ApprovalsPanel";

// Right rail ▸ Access ▸ Approvals: the permanent grants "Always allow (permanent)" mints,
// listed by scope (tools / commands / send targets) with a Remove per row. Backed by
// GET /v1/approvals and POST /v1/approvals/revoke; Remove refetches so the panel
// reflects the store, not an optimistic guess.

const SNAPSHOT = {
  allow_tools: ["send_file"],
  allow_commands: ["git status"],
  allow_targets: { send_message: ["slack:T1/C1"] },
};

type Call = { url: string; init?: RequestInit };
type Snapshot = {
  allow_tools: string[];
  allow_commands: string[];
  allow_targets: Record<string, string[]>;
};

function stubFetch(snapshot: Snapshot = SNAPSHOT): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, init });
      if (url.endsWith("/v1/approvals")) {
        return { ok: true, json: async () => snapshot } as Response;
      }
      if (url.endsWith("/v1/approvals/revoke")) {
        return { ok: true, json: async () => ({ ok: true, ...snapshot }) } as Response;
      }
      // Every other Settings fetch (settings, trusted workspaces, …) gets a benign shape.
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
  return calls;
}

const revokeCalls = (calls: Call[]) => calls.filter((c) => c.url.endsWith("/v1/approvals/revoke"));
const listCalls = (calls: Call[]) =>
  calls.filter((c) => c.url.endsWith("/v1/approvals") && (c.init?.method ?? "GET") === "GET");

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Access rail — Approvals panel", () => {
  it("lists the three grant groups from GET /v1/approvals", async () => {
    stubFetch();
    render(<ApprovalsPanel />);
    await screen.findByText("send_file");
    expect(screen.getByText("git status")).toBeTruthy();
    // A per-target grant renders as one row naming both halves of the rule.
    expect(screen.getByText("send_message → slack:T1/C1")).toBeTruthy();
    expect(screen.getAllByText("Remove")).toHaveLength(3);
  });

  it("shows the empty state when nothing is pre-approved", async () => {
    stubFetch({ allow_tools: [], allow_commands: [], allow_targets: {} });
    render(<ApprovalsPanel />);
    expect(await screen.findByText("Nothing pre-approved yet.")).toBeTruthy();
  });

  it("revokes a command grant with kind=command and refetches the list", async () => {
    const calls = stubFetch();
    render(<ApprovalsPanel />);
    const row = (await screen.findByText("git status")).closest("div")!;
    fireEvent.click(within(row).getByText("Remove"));

    await waitFor(() => expect(revokeCalls(calls)).toHaveLength(1));
    const revoke = revokeCalls(calls)[0];
    expect(revoke.init?.method).toBe("POST");
    expect(JSON.parse(String(revoke.init?.body))).toEqual({ kind: "command", value: "git status" });
    // The card refreshes from the store after revoking (initial GET + one more).
    await waitFor(() => expect(listCalls(calls).length).toBeGreaterThan(1));
  });

  it("revokes a target grant carrying the owning tool", async () => {
    const calls = stubFetch();
    render(<ApprovalsPanel />);
    const row = (await screen.findByText("send_message → slack:T1/C1")).closest("div")!;
    fireEvent.click(within(row).getByText("Remove"));

    await waitFor(() => expect(revokeCalls(calls)).toHaveLength(1));
    expect(JSON.parse(String(revokeCalls(calls)[0].init?.body))).toEqual({
      kind: "target",
      value: "slack:T1/C1",
      tool: "send_message",
    });
  });
});
