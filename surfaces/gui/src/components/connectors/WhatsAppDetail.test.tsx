import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WhatsAppDetail } from "./WhatsAppDetail";
import type { Connector, ContactRow } from "../../api";

// The WhatsApp page's reason to exist is authorizing someone who has NEVER written in:
// by typing a number, or by finding them in the address book. What both paths POST is the
// bare-digits key the webhook produces — these tests assert on the captured body, because
// an unnormalized number would sit on the allow-list matching nobody and the UI would
// still look like it worked.

type Call = { url: string; method: string; body: any };

function stubFetch(contacts: { ok: boolean; error?: string; contacts: ContactRow[] }): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method || "GET").toUpperCase();
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
      if (url.includes("/contacts")) return { ok: true, json: async () => contacts } as Response;
      return { ok: true, json: async () => ({ ok: true }) } as Response;
    }),
  );
  return calls;
}

const allowCalls = (calls: Call[]) => calls.filter((c) => c.url.includes("/allow"));

const CONNECTOR = {
  name: "whatsapp_evolution",
  title: "WhatsApp",
  auth: "token",
  two_way: true,
  channels: false,
  connected: true,
  account: null,
  allowed_users: [],
  recent: [],
  unauthorized: [],
  tools: [],
  fields: [],
} as unknown as Connector;

const row = (over: Partial<ContactRow> = {}): ContactRow => ({
  number: "5511988887777",
  name: "Test Contact",
  display: "+55 11 98888-7777",
  allowed: false,
  ...over,
});

const renderPage = (onChanged = () => {}) =>
  render(
    <WhatsAppDetail c={CONNECTOR} cloud={null} slack={null} onChanged={onChanged} />,
  );

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("WhatsAppDetail", () => {
  it("renders the page for whatsapp_evolution with the allow-list blocks", () => {
    stubFetch({ ok: true, contacts: [] });
    renderPage();
    expect(screen.getByTestId("whatsapp-detail")).toBeTruthy();
    // Add someone comes first: it is why the owner opened the page.
    expect(screen.getByTestId("wa-add-someone")).toBeTruthy();
    // The shared blocks are reused, not reimplemented.
    expect(screen.getByText("Allowed to message")).toBeTruthy();
  });

  it("posts the NORMALIZED digits when a valid number is typed", async () => {
    const calls = stubFetch({ ok: true, contacts: [] });
    const onChanged = vi.fn();
    renderPage(onChanged);

    // Typed the way a Brazilian actually writes it — no country code, punctuation.
    fireEvent.change(screen.getByTestId("wa-number-input"), {
      target: { value: "(11) 99999-9999" },
    });
    fireEvent.click(screen.getByTestId("wa-add-btn"));

    await waitFor(() => expect(allowCalls(calls)).toHaveLength(1));
    const allow = allowCalls(calls)[0];
    expect(allow.method).toBe("POST");
    expect(allow.url).toContain("/v1/connectors/whatsapp_evolution/allow");
    // Not "(11) 99999-9999" and not "11999999999": the key the webhook produces.
    expect(allow.body).toEqual({ user_id: "5511999999999" });
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("posts nothing and shows the reason when the typed number is invalid", async () => {
    const calls = stubFetch({ ok: true, contacts: [] });
    renderPage();

    fireEvent.change(screen.getByTestId("wa-number-input"), { target: { value: "12" } });
    fireEvent.click(screen.getByTestId("wa-add-btn"));

    expect(await screen.findByTestId("wa-invalid")).toBeTruthy();
    expect(screen.getByTestId("wa-invalid").textContent).toBe(
      "That doesn't look like a phone number.",
    );
    // Silence would be indistinguishable from success; nothing may reach the server.
    expect(allowCalls(calls)).toHaveLength(0);
  });

  it("adds a searched contact with its number and name", async () => {
    const calls = stubFetch({ ok: true, contacts: [row()] });
    renderPage();

    fireEvent.change(screen.getByTestId("wa-search-input"), { target: { value: "test" } });
    fireEvent.click(await screen.findByTestId("wa-add-contact"));

    await waitFor(() => expect(allowCalls(calls)).toHaveLength(1));
    // The display name rides along so the allow-list chip is readable at once.
    expect(allowCalls(calls)[0].body).toEqual({
      user_id: "5511988887777",
      name: "Test Contact",
    });
    // The row flips to "Added" rather than offering a second, duplicate add.
    expect(await screen.findByTestId("wa-added")).toBeTruthy();
  });

  it("shows an already-allowed contact as Added rather than an Add button", async () => {
    stubFetch({ ok: true, contacts: [row({ allowed: true })] });
    renderPage();

    fireEvent.change(screen.getByTestId("wa-search-input"), { target: { value: "test" } });
    expect(await screen.findByTestId("wa-added")).toBeTruthy();
    expect(screen.queryByTestId("wa-add-contact")).toBeNull();
  });

  it("shows the error, never an empty state, when the search answers ok:false", async () => {
    // "No contacts" here would claim the address book is empty when the truth is that
    // lookup never ran — and the type-a-number path must still be usable.
    stubFetch({ ok: false, error: "no contact directory", contacts: [] });
    renderPage();

    fireEvent.change(screen.getByTestId("wa-search-input"), { target: { value: "ana" } });
    const err = await screen.findByTestId("wa-search-error");
    expect(err.textContent).toBe("no contact directory");
    expect(screen.queryByTestId("wa-results")).toBeNull();
    expect(screen.getByTestId("wa-number-input")).toBeTruthy();
  });

  it("debounces the search into one request per pause, not per keystroke", async () => {
    // Real timers: fake ones deadlock against the awaits inside the effect. The claim
    // under test is observable either way — four keystrokes in quick succession must
    // produce ONE request, for the last query only.
    const calls = stubFetch({ ok: true, contacts: [row()] });
    renderPage();

    const input = screen.getByTestId("wa-search-input");
    for (const v of ["t", "te", "tes", "test"]) {
      fireEvent.change(input, { target: { value: v } });
    }
    const contactCalls = () => calls.filter((c) => c.url.includes("/contacts"));
    // Nothing fires while the owner is still typing.
    expect(contactCalls()).toHaveLength(0);

    await waitFor(() => expect(contactCalls()).toHaveLength(1), { timeout: 2000 });
    expect(contactCalls()[0].url).toContain("q=test");
  });
});
