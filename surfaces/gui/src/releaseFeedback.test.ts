import { describe, expect, it } from "vitest";
import { translate } from "./i18n";
import { releaseFeedback, releaseTitle } from "./releaseFeedback";

// The Release item was clicked on the row that is open in the transcript.
const OPEN = { id: "s1", title: "Weekly digest", openId: "s1" };
// Clicked on some other row.
const OTHER = { id: "s2", title: "Weekly digest", openId: "s1" };

describe("releaseFeedback", () => {
  it("warns when the server refused because the turn is really alive", () => {
    expect(releaseFeedback({ ok: false, reason: "turn_alive", was_running: true }, OPEN)).toEqual({
      surface: "transcript",
      tone: "warn",
      key: "sidebar.releaseTurnAliveHere",
      vars: { title: "Weekly digest" },
    });
  });

  it("tells the open session to use Stop without asking to open it", () => {
    const here = releaseFeedback({ ok: false, reason: "turn_alive" }, OPEN)!;
    expect(translate("en", here.key, here.vars)).toBe(
      "This session is still running a turn. Use Stop.",
    );
    expect(translate("pt-BR", here.key, here.vars)).toBe(
      "Esta sessão ainda está rodando um turno. Use Parar.",
    );
    const other = releaseFeedback({ ok: false, reason: "turn_alive" }, OTHER)!;
    expect(other.key).toBe("sidebar.releaseTurnAlive");
  });

  it("says so when there was no flag to clear", () => {
    expect(releaseFeedback({ ok: true, was_running: false, queued: 0 }, OPEN)).toEqual({
      surface: "transcript",
      tone: "info",
      key: "sidebar.releaseNothingStuck",
      vars: { title: "Weekly digest" },
    });
  });

  it("stays quiet after a real release: turn_done already updated the screen", () => {
    expect(releaseFeedback({ ok: true, was_running: true, queued: 1 }, OPEN)).toBeNull();
    expect(releaseFeedback({ ok: true, was_running: true, queued: 1 }, OTHER)).toBeNull();
  });

  it("stays quiet on a failure it has no words for", () => {
    expect(releaseFeedback({ ok: false }, OPEN)).toBeNull();
  });

  it("warns, naming the session, when the server answered with an error status", () => {
    const failed = { ok: false, reason: "http_error" as const, status: 500 };
    expect(releaseFeedback(failed, OTHER)).toEqual({
      surface: "toast",
      tone: "warn",
      key: "sidebar.releaseFailed",
      vars: { title: "Weekly digest" },
    });
    expect(translate("en", "sidebar.releaseFailed", { title: "Weekly digest" })).toBe(
      "Could not release “Weekly digest”.",
    );
    expect(translate("pt-BR", "sidebar.releaseFailed", { title: "Weekly digest" })).toBe(
      "Não foi possível liberar “Weekly digest”.",
    );
  });

  it("goes to the transcript only when the released row is the open session", () => {
    const refused = { ok: false, reason: "turn_alive" as const };
    expect(releaseFeedback(refused, OPEN)?.surface).toBe("transcript");
    expect(releaseFeedback(refused, OTHER)?.surface).toBe("toast");
  });

  it("follows the session open when the answer lands, not the one open at click time", () => {
    // Release clicked on s1 while s1 was open, then s2 opened before the server
    // answered. The notice must not land in s2's transcript: toast, naming s1.
    const refused = { ok: false, reason: "turn_alive" as const };
    const left = { id: "s1", title: "Weekly digest", openId: "s2" };
    expect(releaseFeedback(refused, left)).toEqual({
      surface: "toast",
      tone: "warn",
      key: "sidebar.releaseTurnAlive",
      vars: { title: "Weekly digest" },
    });
    // The other way round: clicked on s2 from s1, then s2 opened. It belongs in the
    // transcript now, worded for the session on screen.
    const arrived = { id: "s2", title: "Weekly digest", openId: "s2" };
    expect(releaseFeedback(refused, arrived)?.surface).toBe("transcript");
    expect(releaseFeedback(refused, arrived)?.key).toBe("sidebar.releaseTurnAliveHere");
  });

  it("names the session in both surfaces", () => {
    const refused = { ok: false, reason: "turn_alive" as const };
    expect(releaseFeedback(refused, OPEN)?.vars).toEqual({ title: "Weekly digest" });
    expect(releaseFeedback(refused, OTHER)?.vars).toEqual({ title: "Weekly digest" });
  });

  it("falls back to a short id for an untitled session", () => {
    const untitled = { id: "0123456789ab", openId: "s1" };
    expect(releaseFeedback({ ok: true, was_running: false }, untitled)?.vars).toEqual({
      title: "01234567",
    });
  });

  it("renders a sentence that names the session, in both languages", () => {
    const feedback = releaseFeedback({ ok: false, reason: "turn_alive" }, OTHER)!;
    expect(translate("en", feedback.key, feedback.vars)).toBe(
      "“Weekly digest” is still running a turn. Open it and use Stop.",
    );
    expect(translate("pt-BR", feedback.key, feedback.vars)).toBe(
      "“Weekly digest” ainda está rodando um turno. Abra e use Parar.",
    );
    const quiet = releaseFeedback({ ok: true, was_running: false }, OTHER)!;
    expect(translate("en", quiet.key, quiet.vars)).toBe("Nothing was stuck in “Weekly digest”.");
    expect(translate("pt-BR", quiet.key, quiet.vars)).toBe(
      "Nada estava travado em “Weekly digest”.",
    );
  });
});

describe("releaseTitle", () => {
  it("prefers the title", () => {
    expect(releaseTitle("0123456789ab", "Weekly digest")).toBe("Weekly digest");
  });

  it("uses the first eight characters of the id when there is no title", () => {
    expect(releaseTitle("0123456789ab")).toBe("01234567");
    expect(releaseTitle("0123456789ab", "")).toBe("01234567");
  });

  it("keeps a short id whole", () => {
    expect(releaseTitle("abc")).toBe("abc");
  });
});
