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
    const sources = import.meta.glob("./**/*.tsx", { query: "?raw", import: "default" });
    const offenders: string[] = [];

    for (const [path, load] of Object.entries(sources)) {
      if (path.includes(".test.")) continue;
      const text = (await load()) as string;
      const offersSignIn =
        text.includes("cloudLogin(") || text.includes("CloudSignInInline");
      if (offersSignIn && !text.includes("cloudAvailable")) offenders.push(path);
    }

    expect(offenders, `these offer cloud sign-in without gating it: ${offenders}`).toEqual(
      [],
    );
  });

  it("no surface claims the app runs specifically on a Mac", async () => {
    // The same platform assumption turned up in the loopback footer, the connector
    // catalogue and five GUI strings — it is wrong on Windows every time.
    const sources = import.meta.glob("./**/*.tsx", { query: "?raw", import: "default" });
    const offenders: string[] = [];

    for (const [path, load] of Object.entries(sources)) {
      if (path.includes(".test.")) continue;
      const text = (await load()) as string;
      if (/on this Mac/.test(text)) offenders.push(path);
    }

    expect(offenders, `platform-specific copy: ${offenders}`).toEqual([]);
  });
});
