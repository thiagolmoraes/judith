import { afterEach, describe, expect, it, vi } from "vitest";
import { forceIdleSession } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("forceIdleSession", () => {
  it("POSTs to the session's force-idle route and returns the server's answer", async () => {
    const request = vi.fn(async (_url: string, _init?: RequestInit) => {
      return { json: async () => ({ ok: true, was_running: true }) } as Response;
    });
    vi.stubGlobal("fetch", request);

    const body = await forceIdleSession("s1");

    expect(body).toEqual({ ok: true, was_running: true });
    expect(request).toHaveBeenCalledOnce();
    const [url, init] = request.mock.calls[0];
    expect(url).toMatch(/\/v1\/sessions\/s1\/force-idle$/);
    expect(init?.method).toBe("POST");
    // The normal click carries no body: the server only overrides when asked.
    expect(init?.body).toBeUndefined();
  });

  it("sends {force: true} as JSON when asked to override a live turn", async () => {
    const request = vi.fn(async (_url: string, _init?: RequestInit) => {
      return { status: 200, json: async () => ({ ok: true, was_running: true, queued: 0 }) } as Response;
    });
    vi.stubGlobal("fetch", request);

    await forceIdleSession("s1", { force: true });

    const [, init] = request.mock.calls[0];
    // The auth wrapper rebuilds headers as a Headers instance, so read through one.
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init?.body))).toEqual({ force: true });
  });

  it("turns a 409 into {ok: false, reason: 'turn_alive'} instead of throwing", async () => {
    const request = vi.fn(async () => {
      return {
        status: 409,
        json: async () => ({ ok: false, reason: "turn_alive", was_running: true, queued: 2 }),
      } as Response;
    });
    vi.stubGlobal("fetch", request);

    await expect(forceIdleSession("s1")).resolves.toEqual({
      ok: false,
      reason: "turn_alive",
      was_running: true,
      queued: 2,
    });
  });

  it("a 409 with an empty body still reads as turn_alive", async () => {
    const request = vi.fn(async () => ({ status: 409, json: async () => ({}) }) as Response);
    vi.stubGlobal("fetch", request);

    await expect(forceIdleSession("s1")).resolves.toEqual({ ok: false, reason: "turn_alive" });
  });

  it("escapes the session id in the path", async () => {
    const request = vi.fn(async (_url: string) => ({ json: async () => ({ ok: true }) }) as Response);
    vi.stubGlobal("fetch", request);

    await forceIdleSession("a/b c");

    expect(request.mock.calls[0][0]).toMatch(/\/v1\/sessions\/a%2Fb%20c\/force-idle$/);
  });
});
