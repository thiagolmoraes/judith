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
  });

  it("escapes the session id in the path", async () => {
    const request = vi.fn(async (_url: string) => ({ json: async () => ({ ok: true }) }) as Response);
    vi.stubGlobal("fetch", request);

    await forceIdleSession("a/b c");

    expect(request.mock.calls[0][0]).toMatch(/\/v1\/sessions\/a%2Fb%20c\/force-idle$/);
  });
});
