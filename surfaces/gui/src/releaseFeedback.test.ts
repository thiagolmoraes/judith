import { describe, expect, it } from "vitest";
import { releaseFeedback } from "./releaseFeedback";

describe("releaseFeedback", () => {
  it("warns when the server refused because the turn is really alive", () => {
    expect(releaseFeedback({ ok: false, reason: "turn_alive", was_running: true })).toEqual({
      tone: "warn",
      key: "sidebar.releaseTurnAlive",
    });
  });

  it("says so when there was no flag to clear", () => {
    expect(releaseFeedback({ ok: true, was_running: false, queued: 0 })).toEqual({
      tone: "info",
      key: "sidebar.releaseNothingStuck",
    });
  });

  it("stays quiet after a real release: turn_done already updated the screen", () => {
    expect(releaseFeedback({ ok: true, was_running: true, queued: 1 })).toBeNull();
  });

  it("stays quiet on a failure it has no words for", () => {
    expect(releaseFeedback({ ok: false })).toBeNull();
  });
});
