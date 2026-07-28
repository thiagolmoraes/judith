import { useEffect, useRef, useState } from "react";
import {
  announceCloudChanged,
  cloudAvailable,
  cloudLogin,
  getCloudStatus,
  waitForCloudSignIn,
  type CloudStatus,
} from "../../api";
import { useI18n } from "../../i18n/useLocale";

// The signed-out state of every one-click pane: a REAL sign-in button, not a
// hint pointing at another page. Sign-in completes in the system browser; this
// component then polls until the status flips and broadcasts CLOUD_CHANGED, so
// even poll-less hosts (the Sources rail's inline pane) re-render signed in —
// relying on "some other section's 5s poll" left the rail stuck on the prompt
// (FB-013).
export function CloudSignInInline({ blurb }: { blurb?: string }) {
  const { t } = useI18n();
  const [waiting, setWaiting] = useState(false);
  const cancelRef = useRef<(() => void) | null>(null);
  // Gate here rather than at each of the five call sites: with the cloud switched off the
  // login route refuses, so the button would be a dead end wherever it appeared.
  const [status, setStatus] = useState<CloudStatus | null>(null);
  useEffect(() => {
    let live = true;
    void getCloudStatus()
      .then((s) => live && setStatus(s))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => () => cancelRef.current?.(), []);
  // Render nothing until the check lands, so the button never flashes on a local-only
  // install; `cloudAvailable` treats an older sidecar with no `enabled` field as on.
  if (!cloudAvailable(status)) return null;
  return (
    <div className="space-y-1.5">
      <button
        className="w-full px-3 py-2 rounded-lg border border-accent text-accent text-[13px] font-medium hover:bg-accentSoft/40"
        data-testid="inline-cloud-sign-in"
        onClick={async () => {
          setWaiting(true);
          await cloudLogin();
          cancelRef.current?.();
          cancelRef.current = waitForCloudSignIn((s) => {
            setWaiting(false);
            if (s?.signed_in) announceCloudChanged();
          });
        }}
      >
        {waiting ? t("cloudSignIn.checkBrowser") : t("cloudSignIn.signIn")}
      </button>
      <div className="text-[11.5px] text-faint">
        {blurb || t("cloudSignIn.blurbDefault")}
      </div>
    </div>
  );
}

// The UNKNOWN state: the status fetch hasn't resolved (or is being retried).
// Rendering the sign-in prompt here told signed-in users they weren't (FB-013) —
// pending must look like pending.
export function CloudStatusPending() {
  const { t } = useI18n();
  return (
    <div
      className="text-[12px] text-faint py-2 text-center"
      data-testid="cloud-status-pending"
    >
      {t("cloudSignIn.checking")}
    </div>
  );
}
