import type { ForceIdleResult } from "./api";

export interface ReleaseFeedback {
  tone: "info" | "warn";
  // i18n key of the transcript notice.
  key: string;
}

// What the transcript says after a "Release session" click. Null means the release
// did its job: the server's turn_done already updated the screen, nothing to add.
export function releaseFeedback(result: ForceIdleResult): ReleaseFeedback | null {
  if (result.reason === "turn_alive") return { tone: "warn", key: "sidebar.releaseTurnAlive" };
  if (result.ok && result.was_running === false) {
    return { tone: "info", key: "sidebar.releaseNothingStuck" };
  }
  return null;
}
