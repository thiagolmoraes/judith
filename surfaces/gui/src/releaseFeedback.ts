import type { ForceIdleResult } from "./api";

export interface ReleaseTarget {
  // The row the Release item was clicked on.
  id: string;
  title?: string;
  // The session open in the transcript.
  openId: string;
}

export interface ReleaseFeedback {
  // "transcript" when the released row is the open session. "toast" for any other
  // row: a notice in the open transcript would talk about a session it is not showing.
  surface: "transcript" | "toast";
  tone: "info" | "warn";
  // i18n key. Every text names the session through {title}.
  key: string;
  vars: { title: string };
}

// What a message calls the session. An untitled row gets the start of its id, enough
// to find it in the sidebar.
export function releaseTitle(id: string, title?: string): string {
  return title || id.slice(0, 8);
}

function feedbackFor(result: ForceIdleResult): Pick<ReleaseFeedback, "tone" | "key"> | null {
  if (result.reason === "turn_alive") return { tone: "warn", key: "sidebar.releaseTurnAlive" };
  if (result.reason === "http_error") return { tone: "warn", key: "sidebar.releaseFailed" };
  if (result.ok && result.was_running === false) {
    return { tone: "info", key: "sidebar.releaseNothingStuck" };
  }
  return null;
}

// What to say after a "Release session" click, and where. Null means the release did
// its job: the server's turn_done already updated the screen, nothing to add.
export function releaseFeedback(
  result: ForceIdleResult,
  target: ReleaseTarget,
): ReleaseFeedback | null {
  const found = feedbackFor(result);
  if (!found) return null;
  const surface = target.id === target.openId ? "transcript" : "toast";
  // In the open transcript "open it" makes no sense: the session is already on screen.
  const key =
    found.key === "sidebar.releaseTurnAlive" && surface === "transcript"
      ? "sidebar.releaseTurnAliveHere"
      : found.key;
  return {
    ...found,
    key,
    surface,
    vars: { title: releaseTitle(target.id, target.title) },
  };
}
