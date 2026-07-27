import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ByoSetup, byoSupported } from "./ByoSetup";
import type { Connector } from "../../api";

// A hermetic fetch stub routing by URL substring + method. Records calls so tests can assert POSTs.
type Call = { url: string; method: string; body: any };

function stubFetch(routes: { match: string; method?: string; json: any }[]) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method || "GET").toUpperCase();
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    for (const r of routes) {
      if (url.includes(r.match) && (!r.method || r.method === method)) {
        return { ok: true, json: async () => r.json } as Response;
      }
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const connector = (name: string, title: string): Connector =>
  ({ name, title, fields: [], tools: [] }) as unknown as Connector;

const REDIRECT = "http://127.0.0.1:50055/oauth/callback";
const EMPTY = {
  oauth: {},
  github: { configured: false, app_id: "" },
  redirect_uri: REDIRECT,
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("byoSupported", () => {
  it("covers the OAuth connectors and excludes the rest", () => {
    expect(byoSupported("github")).toBe(true);
    expect(byoSupported("gmail")).toBe(true);
    expect(byoSupported("notion")).toBe(true);
    // No BYO path server-side, so the tab must not appear.
    expect(byoSupported("datadog")).toBe(false);
  });
});

describe("ByoSetup — unconfigured", () => {
  it("shows the registration guide and the fields the provider needs", async () => {
    stubFetch([{ match: "/v1/connectors/byo", json: EMPTY }]);
    render(<ByoSetup c={connector("gmail", "Gmail")} onConnected={() => {}} />);

    await screen.findByTestId("byo-client-id");
    expect(screen.getByTestId("byo-client-secret")).toBeTruthy();
    // The exact callback URL matters, and it must come from the server: the packaged
    // sidecar binds a random port, so a hardcoded one would never match.
    expect(screen.getByTestId("byo-redirect-uri").textContent).toBe(REDIRECT);
  });

  it("asks GitHub for an App id and private key, not a client secret", async () => {
    stubFetch([{ match: "/v1/connectors/byo", json: EMPTY }]);
    render(<ByoSetup c={connector("github", "GitHub")} onConnected={() => {}} />);

    await screen.findByTestId("byo-app-id");
    expect(screen.getByTestId("byo-private-key")).toBeTruthy();
    expect(screen.queryByTestId("byo-client-secret")).toBeNull();
  });

  it("keeps save disabled until the identifying field is filled", async () => {
    stubFetch([{ match: "/v1/connectors/byo", json: EMPTY }]);
    render(<ByoSetup c={connector("notion", "Notion")} onConnected={() => {}} />);

    const save = (await screen.findByTestId("byo-save")) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByTestId("byo-client-id"), { target: { value: "cid" } });
    expect(save.disabled).toBe(false);
  });

  it("posts the credentials and omits scopes when left blank", async () => {
    const calls = stubFetch([
      { match: "/v1/connectors/byo", method: "GET", json: EMPTY },
      { match: "/byo-config", method: "POST", json: { ok: true, configured: true } },
    ]);
    render(<ByoSetup c={connector("notion", "Notion")} onConnected={() => {}} />);

    fireEvent.change(await screen.findByTestId("byo-client-id"), { target: { value: "cid" } });
    fireEvent.change(screen.getByTestId("byo-client-secret"), { target: { value: "sec" } });
    fireEvent.click(screen.getByTestId("byo-save"));

    await waitFor(() => {
      const post = calls.find((c) => c.url.includes("/byo-config"));
      expect(post?.body.fields).toEqual({ client_id: "cid", client_secret: "sec" });
    });
  });

  it("surfaces a save error instead of pretending it worked", async () => {
    stubFetch([
      { match: "/v1/connectors/byo", method: "GET", json: EMPTY },
      { match: "/byo-config", method: "POST", json: { ok: false, error: "client_secret required" } },
    ]);
    render(<ByoSetup c={connector("notion", "Notion")} onConnected={() => {}} />);

    fireEvent.change(await screen.findByTestId("byo-client-id"), { target: { value: "cid" } });
    fireEvent.click(screen.getByTestId("byo-save"));
    expect(await screen.findByText("client_secret required")).toBeTruthy();
  });
});

describe("ByoSetup — configured", () => {
  const CONFIGURED = {
    oauth: { notion: { configured: true, client_id: "cid-stored", scopes: ["a", "b"] } },
    github: { configured: false, app_id: "" },
    redirect_uri: REDIRECT,
  };

  it("shows the stored app and offers connect rather than the form", async () => {
    stubFetch([{ match: "/v1/connectors/byo", json: CONFIGURED }]);
    render(<ByoSetup c={connector("notion", "Notion")} onConnected={() => {}} />);

    await screen.findByTestId("byo-connect");
    expect(screen.getByText("cid-stored")).toBeTruthy();
    expect(screen.queryByTestId("byo-client-id")).toBeNull();
  });

  it("never renders a client secret — the API doesn't return one", async () => {
    stubFetch([{ match: "/v1/connectors/byo", json: CONFIGURED }]);
    const { container } = render(
      <ByoSetup c={connector("notion", "Notion")} onConnected={() => {}} />,
    );
    await screen.findByTestId("byo-connect");
    expect(container.textContent).not.toContain("sec");
  });

  it("starts the connect and reports back", async () => {
    const onConnected = vi.fn();
    const calls = stubFetch([
      { match: "/v1/connectors/byo", method: "GET", json: CONFIGURED },
      { match: "/byo-connect", method: "POST", json: { ok: true, authorize_url: "https://x" } },
    ]);
    render(<ByoSetup c={connector("notion", "Notion")} onConnected={onConnected} />);

    fireEvent.click(await screen.findByTestId("byo-connect"));
    await waitFor(() => expect(onConnected).toHaveBeenCalled());
    expect(calls.some((c) => c.url.includes("/byo-connect") && c.method === "POST")).toBe(true);
  });

  it("does not report success when the connect fails", async () => {
    const onConnected = vi.fn();
    stubFetch([
      { match: "/v1/connectors/byo", method: "GET", json: CONFIGURED },
      { match: "/byo-connect", method: "POST", json: { ok: false, error: "no app configured" } },
    ]);
    render(<ByoSetup c={connector("notion", "Notion")} onConnected={onConnected} />);

    fireEvent.click(await screen.findByTestId("byo-connect"));
    expect(await screen.findByText("no app configured")).toBeTruthy();
    expect(onConnected).not.toHaveBeenCalled();
  });

  it("clears the app with a blank identifier, which is how the server deletes it", async () => {
    const calls = stubFetch([
      { match: "/v1/connectors/byo", method: "GET", json: CONFIGURED },
      { match: "/byo-config", method: "POST", json: { ok: true, configured: false } },
    ]);
    render(<ByoSetup c={connector("notion", "Notion")} onConnected={() => {}} />);

    fireEvent.click(await screen.findByText("Remove"));
    await waitFor(() => {
      const post = calls.find((c) => c.url.includes("/byo-config"));
      expect(post?.body.fields).toEqual({ client_id: "" });
    });
  });

  it("lets the user change a stored app and re-shows the form", async () => {
    stubFetch([{ match: "/v1/connectors/byo", json: CONFIGURED }]);
    render(<ByoSetup c={connector("notion", "Notion")} onConnected={() => {}} />);

    fireEvent.click(await screen.findByText("Change"));
    // Prefilled from what's stored: retyping an identifier that was just on screen, only
    // to rotate a secret, is needless friction.
    expect((screen.getByTestId("byo-client-id") as HTMLInputElement).value).toBe("cid-stored");
    expect((screen.getByTestId("byo-scopes") as HTMLInputElement).value).toBe("a b");
    // The secret stays blank and masked — blank means "keep the stored one".
    const secret = screen.getByTestId("byo-client-secret") as HTMLInputElement;
    expect(secret.value).toBe("");
    expect(secret.placeholder).toContain("leave blank to keep");
  });
});

describe("ByoSetup — GitHub", () => {
  const GH = {
    oauth: {},
    github: { configured: true, app_id: "424242" },
    redirect_uri: REDIRECT,
  };

  it("prefills the App id when changing a stored App", async () => {
    stubFetch([{ match: "/v1/connectors/byo", json: GH }]);
    render(<ByoSetup c={connector("github", "GitHub")} onConnected={() => {}} />);

    fireEvent.click(await screen.findByText("Change"));
    expect((screen.getByTestId("byo-app-id") as HTMLInputElement).value).toBe("424242");
    // The key is write-only: it's never returned, so it can't be prefilled.
    expect((screen.getByTestId("byo-private-key") as HTMLTextAreaElement).value).toBe("");
  });

  it("clears a stored App with a blank app_id", async () => {
    const calls = stubFetch([
      { match: "/v1/connectors/byo", method: "GET", json: GH },
      { match: "/byo-config", method: "POST", json: { ok: true, configured: false } },
    ]);
    render(<ByoSetup c={connector("github", "GitHub")} onConnected={() => {}} />);

    fireEvent.click(await screen.findByText("Remove"));
    await waitFor(() => {
      const post = calls.find((c) => c.url.includes("/byo-config"));
      expect(post?.body.fields).toEqual({ app_id: "" });
    });
  });

  it("explains that connect opens the App's install page", async () => {
    stubFetch([{ match: "/v1/connectors/byo", json: GH }]);
    render(<ByoSetup c={connector("github", "GitHub")} onConnected={() => {}} />);
    await screen.findByTestId("byo-connect");
    expect(screen.getByText(/install page/)).toBeTruthy();
  });
});
