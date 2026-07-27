import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { cloudAvailable, type CloudStatus } from "../api";
import { GalleryModal } from "./GalleryModal";

// With OpenWorker Cloud switched off, no surface may offer to sign in: the route refuses,
// so every prompt is a dead end. The gating lives in several components, and the first
// pass missed four of them — the sidebar account menu and footer, the Gallery, the
// automation quickstart, and the onboarding band — so this file pins the predicate,
// sweeps for call sites that skip it, and renders the two trickiest surfaces.

const OFF: CloudStatus = {
  enabled: false,
  signed_in: false,
  account: "",
  user_id: "",
};
const ON_SIGNED_OUT: CloudStatus = {
  enabled: true,
  signed_in: false,
  account: "",
  user_id: "",
};
const LEGACY = {
  // An older sidecar predating the flag: no `enabled` field at all.
  signed_in: false,
  account: "",
  user_id: "",
} as CloudStatus;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("cloudAvailable", () => {
  it("is false when the cloud is switched off", () => {
    expect(cloudAvailable(OFF)).toBe(false);
  });

  it("is true when the cloud is on, signed in or not", () => {
    expect(cloudAvailable(ON_SIGNED_OUT)).toBe(true);
    expect(cloudAvailable({ ...ON_SIGNED_OUT, signed_in: true })).toBe(true);
  });

  it("treats a missing `enabled` as on, for an older sidecar", () => {
    // Absent field must not silently disable the cloud for someone who is using it.
    expect(cloudAvailable(LEGACY)).toBe(true);
  });

  it("is false before the status has loaded", () => {
    // Rendering a sign-in button and then hiding it reads as a flicker; staying quiet
    // until the answer arrives does not. This also makes `null` unsuitable for deciding
    // "the cloud is off" — see the rendered tests below.
    expect(cloudAvailable(null)).toBe(false);
  });
});

/** Source with comments and string literals removed.
 *
 * A substring search over raw source counts a mention in a comment as a real call — and
 * the comment explaining this very gate did exactly that in GalleryModal. Crude but
 * sufficient: the guard only needs to know whether an identifier survives once the prose
 * is gone. */
function code(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ") // block comments, including JSX {/* … */}
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1 ") // line comments, sparing the // in URLs
    .replace(/`(?:[^`\\]|\\.)*`/g, "``") // template literals
    .replace(/"(?:[^"\\]|\\.)*"/g, '""')
    .replace(/'(?:[^'\\]|\\.)*'/g, "''");
}

describe("cloud sign-in surfaces", () => {
  it("every component that offers sign-in consults the predicate", async () => {
    // A grep-style guard, deliberately: the bug was a *missing* call site, which no
    // amount of testing the components that do gate could have caught. `../**` so the
    // sweep reaches all of src/ — providers/ sat outside the original glob.
    const sources = import.meta.glob("../**/*.tsx", { query: "?raw", import: "default" });
    const offenders: string[] = [];

    for (const [path, load] of Object.entries(sources)) {
      if (path.includes(".test.")) continue;
      const text = code((await load()) as string);
      const offersSignIn =
        text.includes("cloudLogin(") || text.includes("CloudSignInInline");
      // With the paren, so an unused import doesn't satisfy the check either.
      if (offersSignIn && !text.includes("cloudAvailable(")) offenders.push(path);
    }

    expect(offenders, `these offer cloud sign-in without gating it: ${offenders}`).toEqual(
      [],
    );
  });

  it("no surface claims the app runs specifically on a Mac", async () => {
    // The same platform assumption turned up in the loopback footer, the connector
    // catalogue and five GUI strings — it is wrong on Windows every time.
    const sources = import.meta.glob("../**/*.tsx", { query: "?raw", import: "default" });
    const offenders: string[] = [];

    for (const [path, load] of Object.entries(sources)) {
      if (path.includes(".test.")) continue;
      // Whitespace-insensitive: JSX wraps prose across lines, so "stay on\n this Mac"
      // is one sentence to a reader and two lines to a naive regex. Both live instances
      // were split exactly that way and a line-oriented search missed them.
      const text = ((await load()) as string).replace(/\s+/g, " ");
      if (/\bthis Mac\b/.test(text)) offenders.push(path);
    }

    expect(offenders, `platform-specific copy: ${offenders}`).toEqual([]);
  });
});

function stubCloud(status: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.includes("/v1/cloud/status")) {
        if (status instanceof Error) throw status;
        return { ok: true, json: async () => status } as Response;
      }
      // The gallery fetch has to resolve in the shape reload() expects, or the component
      // stays on its loading branch and never reaches the one under test.
      return {
        ok: true,
        json: async () => ({ ok: true, personas: [], items: [] }),
      } as Response;
    }),
  );
}

describe("unknown status is not disabled status", () => {
  it("does not claim the cloud is off when the status fetch fails", async () => {
    // `cloud` stays null, and cloudAvailable(null) is false — so a branch written against
    // the predicate would report a dropped request as "the cloud is switched off", sending
    // the user after a setting they never changed. Keying on `enabled === false` instead
    // leaves the pre-existing unknown-state rendering alone.
    stubCloud(new Error("offline"));
    render(<GalleryModal onClose={() => {}} onInstalled={() => {}} />);
    await waitFor(() => expect(screen.queryByTestId("gallery-loading")).toBeNull());
    expect(screen.queryByTestId("gallery-unavailable")).toBeNull();
  });

  it("shows the disabled copy only when the cloud reports enabled: false", async () => {
    stubCloud({ enabled: false, signed_in: false, account: "", user_id: "" });
    render(<GalleryModal onClose={() => {}} onInstalled={() => {}} />);
    await waitFor(() => expect(screen.queryByTestId("gallery-unavailable")).toBeTruthy());
    expect(screen.queryByTestId("gallery-signin")).toBeNull();
  });

  it("shows neither once the cloud is on and signed in", async () => {
    stubCloud({ enabled: true, signed_in: true, account: "a@b.c", user_id: "u" });
    render(<GalleryModal onClose={() => {}} onInstalled={() => {}} />);
    await waitFor(() => expect(screen.queryByTestId("gallery-signin")).toBeNull());
    expect(screen.queryByTestId("gallery-unavailable")).toBeNull();
  });
});
