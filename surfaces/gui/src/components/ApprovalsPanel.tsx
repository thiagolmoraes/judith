// Persistent pre-approvals — the standing grants "Always allow (permanent)" mints on
// approval cards, in the three scopes the server routes them into: tool-wide, exact
// shell command, per-target. Lives in the right rail's Access section (owner ask
// 2026-07-31: approvals are access control, so they belong beside Sources/Folders,
// not buried in Settings). Remove re-arms the question — the next matching action
// simply asks again.
import { useEffect, useState } from "react";
import {
  getApprovals,
  revokeApproval,
  type ApprovalsSnapshot,
} from "../api";
import { useI18n } from "../i18n/useLocale";

export function ApprovalsPanel() {
  const { t } = useI18n();
  const [snap, setSnap] = useState<ApprovalsSnapshot | null>(null);
  // A failed load must NEVER read as "nothing pre-approved" — that would tell the
  // user grants are gone while they still gate nothing. Keep the last good
  // snapshot on failure and surface the error separately.
  const [failed, setFailed] = useState(false);

  const refresh = () =>
    getApprovals()
      .then((s) => {
        setSnap(s);
        setFailed(false);
      })
      .catch(() => setFailed(true));

  useEffect(() => {
    refresh();
  }, []);

  const revoke = async (kind: "tool" | "command" | "target", value: string, tool?: string) => {
    // Refresh even when the call fails: the list re-syncs with the server's truth,
    // so a failed removal visibly stays in the list instead of silently lingering
    // out of view.
    try {
      await revokeApproval(kind, value, tool);
    } finally {
      refresh();
    }
  };

  const targetRows = Object.entries(snap?.allow_targets ?? {}).flatMap(([tool, targets]) =>
    targets.map((target) => ({
      key: `${tool} ${target}`,
      text: `${tool} → ${target}`,
      remove: () => void revoke("target", target, tool),
    })),
  );
  const empty =
    !!snap && !snap.allow_tools.length && !snap.allow_commands.length && !targetRows.length;

  if (snap === null) {
    if (failed) {
      return (
        <div className="text-[12px] text-muted">
          {t("settings.approvals.loadError")}{" "}
          <button className="text-accent hover:underline" onClick={() => void refresh()}>
            {t("common.retry")}
          </button>
        </div>
      );
    }
    return <div className="text-[12px] text-muted">{t("common.loading")}</div>;
  }
  if (empty) {
    return <div className="text-[12px] text-muted">{t("settings.approvals.empty")}</div>;
  }
  return (
    <>
      {failed && (
        <div className="text-[11.5px] text-muted">{t("settings.approvals.loadError")}</div>
      )}
      <ApprovalGroup
        label={t("settings.approvals.tools")}
        rows={snap.allow_tools.map((tool) => ({
          key: tool,
          text: tool,
          remove: () => void revoke("tool", tool),
        }))}
      />
      <ApprovalGroup
        label={t("settings.approvals.commands")}
        rows={snap.allow_commands.map((command) => ({
          key: command,
          text: command,
          remove: () => void revoke("command", command),
        }))}
      />
      <ApprovalGroup label={t("settings.approvals.targets")} rows={targetRows} />
    </>
  );
}

function ApprovalGroup({
  label,
  rows,
}: {
  label: string;
  rows: { key: string; text: string; remove: () => void }[];
}) {
  const { t } = useI18n();
  if (!rows.length) return null;
  return (
    <div className="mt-1.5">
      <div className="text-[11px] text-faint">{label}</div>
      <div className="divide-y divide-line">
        {rows.map((row) => (
          <div key={row.key} className="py-1.5 flex items-center gap-2">
            <code className="min-w-0 flex-1 text-[12px] text-ink break-all">{row.text}</code>
            <button
              className="text-[11.5px] text-red-600 px-1.5 py-0.5"
              aria-label={`${t("settings.approvals.remove")}: ${row.text}`}
              onClick={row.remove}
            >
              {t("settings.approvals.remove")}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
