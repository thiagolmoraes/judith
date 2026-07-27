import { describe, expect, it } from "vitest";
import { cloudAvailable, type CloudStatus } from "../api";

// With OpenWorker Cloud switched off, no surface may offer to sign in: the route refuses,
// so every prompt is a dead end. The gating lives in several components, and the first
// pass missed four of them — the sidebar account menu and footer, the Gallery, the
// automation quickstart, and the onboarding band — so this file pins the predicate and
// documents where it has to be applied.

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
const LEGACY: CloudStatus = {
  // An older sidecar predating the flag: no `enabled` field at all.
  signed_in: false,
  account: "",
  user_id: "",
} as CloudStatus;

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
    // until the answer arrives does not.
    expect(cloudAvailable(null)).toBe(false);
  });
});

describe("cloud sign-in surfaces", () => {
  it("every component that offers sign-in consults the predicate", async () => {
    // A grep-style guard, deliberately: the bug was a *missing* call site, which no
    // amount of testing the components that do call it would have caught.
    // `../**` so the sweep reaches every .tsx under src/, not just components/ —
    // providers/ProviderSetup.tsx sat outside the original glob.
    const sources = import.meta.glob("../**/*.tsx", { query: "?raw", import: "default" });
    const offenders: string[] = [];

    for (const [path, load] of Object.entries(sources)) {
      if (path.includes(".test.")) continue;
      const text = (await load()) as string;
      const offersSignIn =
        text.includes("cloudLogin(") || text.includes("CloudSignInInline");
      // `cloudAvailable(` with the paren: an import alone satisfies a substring check
      // while gating nothing, which is the exact failure this guard exists to catch.
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
      const text = (await load()) as string;
      if (/on this Mac/.test(text)) offenders.push(path);
    }

    expect(offenders, `platform-specific copy: ${offenders}`).toEqual([]);
  });
});

describe("unknown status is not disabled status", () => {
  it("distinguishes a failed fetch from a switched-off cloud", () => {
    // `null` means the status fetch failed or hasn't landed. Surfaces that explain "the
    // cloud is off" must test `enabled === false` instead, or a transient network error
    // sends the user hunting for a setting they never changed.
    expect(cloudAvailable(null)).toBe(false); // don't offer sign-in yet
    expect(OFF.enabled).toBe(false); // ...but only this one means "switched off"
    expect((null as unknown as CloudStatus | null)?.enabled).toBeUndefined();
  });

  it("the Gallery's unavailable copy keys on enabled === false", async () => {
    const source = (await import("./GalleryModal.tsx?raw")).default as string;
    expect(source).toContain('cloud?.enabled === false');
    // Would reintroduce the bug: null (unknown) would take the disabled branch.
    expect(source).not.toContain("!cloudAvailable(cloud) ? (");
  });
});

describe("no dead affordances", () => {
  it("the quickstart's Connect is replaced, not just unexplained, when the cloud is off", async () => {
    // startConnect() sets pendingConn and returns when signed out; with the pane hidden
    // that made the button do visibly nothing.
    const source = (await import("./AutomationQuickstart.tsx?raw")).default as string;
    expect(source).toContain("ob-connect-unavailable-");
  });
});
