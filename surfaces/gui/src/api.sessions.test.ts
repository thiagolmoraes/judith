import { afterEach, describe, expect, it, vi } from "vitest";
import { forceIdleSession } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("forceIdleSession", () => {
  it("POSTs to the session's force-idle route and returns the server's answer", async () => {
    const request = vi.fn(async (_url: string, _init?: RequestInit) => {
      return { ok: true, status: 200, json: async () => ({ ok: true, was_running: true }) } as Response;
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
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, was_running: true, queued: 0 }),
      } as Response;
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

  it.each([401, 500, 502])("turns a %i into {ok: false, reason: 'http_error'} without reading the body", async (status) => {
    // A proxy page or a crashed route does not answer in JSON. Reading it would throw
    // and the click would look like a network failure with nothing said on screen.
    const request = vi.fn(async () => {
      return {
        ok: false,
        status,
        json: async () => {
          throw new SyntaxError("not JSON");
        },
      } as unknown as Response;
    });
    vi.stubGlobal("fetch", request);

    await expect(forceIdleSession("s1")).resolves.toEqual({ ok: false, reason: "http_error", status });
  });

  it("turns a fetch that never got an answer into {ok: false, reason: 'http_error'}", async () => {
    // Sidecar down or connection refused: fetch rejects instead of answering. The caller
    // reads the same shape as any other failure, so the click still says something on
    // screen instead of returning in silence.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );

    await expect(forceIdleSession("s1")).resolves.toEqual({ ok: false, reason: "http_error" });
  });

  it("escapes the session id in the path", async () => {
    const request = vi.fn(
      async (_url: string) => ({ ok: true, status: 200, json: async () => ({ ok: true }) }) as Response,
    );
    vi.stubGlobal("fetch", request);

    await forceIdleSession("a/b c");

    expect(request.mock.calls[0][0]).toMatch(/\/v1\/sessions\/a%2Fb%20c\/force-idle$/);
  });
});
