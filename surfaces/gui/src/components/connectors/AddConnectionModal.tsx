import { useEffect, useState } from "react";
import {
  cloudAvailable,
  connectConnector,
  connectManaged,
  connectMcpBacked,
  getConnectors,
  type CloudStatus,
  type Connector,
} from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { ConnectSetup } from "../ManageTabs";
import { ByoSetup, byoSupported } from "./ByoSetup";
import { CloudSignInInline, CloudStatusPending } from "./CloudSignIn";
import { PILL_ACCENT, PILL_LINE, TAG_ACCENT } from "./ui";
import { useI18n } from "../../i18n/useLocale";

type Pane = "one" | "byo" | "manual";

const PANE_LABEL: Record<Pane, string> = {
  one: "One click",
  byo: "Your own app",
  manual: "Manual",
};

// The ONE place a connection gets added (UX-DECISIONS §21): the detail page's header
// button (or the list's Connect pill) opens this sheet. Connectors with two connect
// modes get a One click | Manual pill switcher; single-mode connectors render their
// existing ConnectSetup directly (Gmail's managed flow skips the modal entirely).

const INPUT =
  "w-full px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";

export function AddConnectionModal({
  c,
  cloud,
  title,
  onClose,
  onChanged,
}: {
  c: Connector;
  cloud: CloudStatus | null;
  title?: string; // e.g. "Add a workspace" — defaults to "Connect {title}"
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useI18n();
  // MCP-backed one-click (§42): local OAuth against the vendor's hosted MCP server —
  // with manual fields alongside (jira, asana) it's a second mode; alone (monday)
  // it IS the connect flow.
  const mcpBacked = !!c.mcp;
  // The managed one-click needs OpenWorker Cloud. With it switched off the pane would be
  // marketing copy above a button that can't work, so drop the tab entirely — except for
  // MCP-backed connectors, whose one-click is local OAuth and needs no cloud at all.
  const managedOneClick =
    c.name === "slack" ||
    c.name === "hubspot" ||
    c.name === "github" ||
    c.name === "notion" ||
    c.name === "attio";
  const twoModes =
    (managedOneClick && cloudAvailable(cloud)) || (mcpBacked && c.fields.length > 0);
  // A third mode wherever the user can register their own OAuth app: one-click without a
  // cloud sign-in. Offered alongside the other two rather than replacing either.
  const hasByo = byoSupported(c.name);
  const panes: Pane[] = [
    ...(twoModes ? (["one"] as const) : []),
    ...(hasByo ? (["byo"] as const) : []),
    ...(twoModes || hasByo ? (["manual"] as const) : []),
  ];
  const showTabs = panes.length > 1;
  // Default to one-click where it exists, otherwise the user's own app: both beat asking
  // someone to paste a long-lived token by hand.
  const [pane, setPane] = useState<Pane>(twoModes ? "one" : "byo");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-40" data-testid="add-connection-modal">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div
        className="absolute left-1/2 top-[14%] -translate-x-1/2 w-[480px] max-w-[calc(100vw-2rem)] bg-panel rounded-2xl border border-line shadow-2xl"
        role="dialog"
        aria-label={title || `Connect ${c.title}`}
      >
        <div className="flex items-center gap-3 px-5 pt-5">
          <ConnectorBadge connector={c} size={34} title={c.title} />
          <div className="flex-1 font-semibold text-[16px] tracking-tight">
            {title || `Connect ${c.title}`}
          </div>
          <button className="text-faint hover:text-ink text-[18px] leading-none" onClick={onClose} title={t("addConn.close")}>
            ×
          </button>
        </div>

        {showTabs ? (
          <>
            <div className="px-5 pt-4">
              <div className="inline-flex rounded-full p-0.5 bg-paper text-[12.5px] font-medium">
                {panes.map((p) => (
                  <button
                    key={p}
                    data-testid={`modal-pane-${p}`}
                    className={
                      "px-3.5 py-1 rounded-full " +
                      (pane === p ? "bg-panel shadow-sm text-ink border border-line" : "text-muted")
                    }
                    onClick={() => setPane(p)}
                  >
                    {PANE_LABEL[p]}
                  </button>
                ))}
              </div>
            </div>
            {pane === "one" ? (
              mcpBacked ? (
                <McpOneClick c={c} onConnected={() => { onChanged(); onClose(); }} />
              ) : c.name === "hubspot" ? (
                <HubSpotOneClick c={c} cloud={cloud} />
              ) : c.name === "github" ? (
                <GithubOneClick c={c} cloud={cloud} />
              ) : c.name === "slack" ? (
                <SlackOneClick c={c} cloud={cloud} />
              ) : (
                <GenericOneClick c={c} cloud={cloud} />
              )
            ) : pane === "byo" ? (
              <ByoSetup c={c} onConnected={() => { onChanged(); onClose(); }} />
            ) : c.name === "slack" ? (
              <SlackManual onConnected={() => { onChanged(); onClose(); }} />
            ) : (
              <div className="px-1.5 pb-2">
                <ConnectSetup c={c} cloud={cloud} onConnected={() => { onChanged(); onClose(); }} manualOnly />
              </div>
            )}
          </>
        ) : mcpBacked ? (
          /* MCP-backed with no manual fields (monday): one-click IS the flow. */
          <McpOneClick c={c} onConnected={() => { onChanged(); onClose(); }} />
        ) : (
          <div className="px-1.5 pb-2">
            {/* Existing combined setup (managed button + manual fields) for everything else. */}
            <ConnectSetup c={c} cloud={cloud} onConnected={() => { onChanged(); onClose(); }} />
          </div>
        )}
      </div>
    </div>
  );
}

// One-click pane for MCP-BACKED connectors (monday, asana, jira — §42): the sidecar
// runs a fully LOCAL OAuth flow against the vendor's hosted MCP server (DCR — no
// client secret, no broker, no OpenWorker sign-in required). Poll until the card
// flips to connected, then close.
function McpOneClick({ c, onConnected }: { c: Connector; onConnected: () => void }) {
  const { t } = useI18n();
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!waiting) return;
    const t = setInterval(async () => {
      try {
        const list = await getConnectors();
        if (list.find((x) => x.name === c.name)?.connected) onConnected();
      } catch {
        /* keep polling */
      }
    }, 2000);
    return () => clearInterval(t);
  }, [waiting, c.name, onConnected]);
  const go = async () => {
    setError(null);
    const res = await connectMcpBacked(c.name);
    if (res.ok) setWaiting(true);
    else setError(res.error || "could not start the connect");
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <p className="text-[13px] text-muted">
        Opens {c.title} in your browser — sign in and approve access there. No tokens
        typed, and no OpenWorker account needed: the sign-in runs entirely on this
        computer.
      </p>
      <button
        className={PILL_ACCENT + " w-full !py-2"}
        data-testid="modal-mcp-one-click"
        onClick={go}
        disabled={waiting}
      >
        {waiting ? t("mt.checkBrowser") : `Connect ${c.title}`}
      </button>
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-faint text-center flex items-center justify-center gap-1.5">
        <span className={TAG_ACCENT}>{t("addConn.recommended")}</span>{" "}
        {t("addConn.mcpBlurb", { connector: c.title })}
      </p>
    </div>
  );
}

// One-click pane for generic managed connectors (Notion, Attio, …): sign in
// with the service in the browser; each consent lands as its own account.
function GenericOneClick({ c, cloud }: { c: Connector; cloud: CloudStatus | null }) {
  const { t } = useI18n();
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setError(null);
    const res = await connectManaged(c.name);
    if (res.ok) setWaiting(true);
    else setError(res.error || "could not start the connect");
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <p className="text-[13px] text-muted">
        Opens {c.title} in your browser — approve access there. No tokens typed; connect
        again with another account to add it alongside.
      </p>
      {cloud?.signed_in ? (
        <button
          className={PILL_ACCENT + " w-full !py-2"}
          data-testid="modal-generic-one-click"
          onClick={go}
          disabled={waiting}
        >
          {waiting ? "Check your browser…" : `Connect ${c.title}`}
        </button>
      ) : cloud ? (
        <CloudSignInInline />
      ) : (
        <CloudStatusPending />
      )}
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-faint text-center flex items-center justify-center gap-1.5">
        <span className={TAG_ACCENT}>{t("addConn.recommended")}</span>{" "}
        {t("addConn.tokensLocal")}
      </p>
    </div>
  );
}

function SlackOneClick({ c, cloud }: { c: Connector; cloud: CloudStatus | null }) {
  const { t } = useI18n();
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setError(null);
    const res = await connectManaged(c.name);
    if (res.ok) setWaiting(true);
    else setError(res.error || "could not start the install");
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <p className="text-[13px] text-muted">
        Opens Slack in your browser — approve @ocw for the workspace. No tokens; works for any
        number of workspaces.
      </p>
      {cloud?.signed_in ? (
        <button className={PILL_ACCENT + " w-full !py-2"} data-testid="modal-add-to-slack" onClick={go} disabled={waiting}>
          {waiting ? t("mt.checkBrowser") : t("addConn.addToSlack")}
        </button>
      ) : cloud ? (
        <CloudSignInInline />
      ) : (
        <CloudStatusPending />
      )}
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-faint text-center flex items-center justify-center gap-1.5">
        <span className={TAG_ACCENT}>{t("addConn.recommended")}</span>{" "}
        {t("addConn.relayLocal")}
      </p>
    </div>
  );
}

function GithubOneClick({ c, cloud }: { c: Connector; cloud: CloudStatus | null }) {
  const { t } = useI18n();
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setError(null);
    const res = await connectManaged(c.name);
    if (res.ok) setWaiting(true);
    else setError(res.error || "could not start the install");
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <p className="text-[13px] text-muted">
        Opens GitHub in your browser — approve OpenWorker there. An existing @ocw-agent App
        installation links right up; otherwise you'll pick an account and repos. No tokens
        typed; the agent acts as ocw-agent[bot].
      </p>
      {cloud?.signed_in ? (
        /* One button: the broker is authorize-first — it links an existing installation or
           redirects the same tab on to the install page (the old "Already installed? Link
           it" question and the Configure dead-end are gone). */
        <button className={PILL_ACCENT + " w-full !py-2"} data-testid="modal-install-github-app" onClick={() => go()} disabled={waiting}>
          {waiting ? t("mt.checkBrowser") : t("addConn.connectGithub")}
        </button>
      ) : cloud ? (
        <CloudSignInInline />
      ) : (
        <CloudStatusPending />
      )}
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-faint text-center flex items-center justify-center gap-1.5">
        <span className={TAG_ACCENT}>{t("addConn.recommended")}</span>{" "}
        {t("addConn.relayShortLived")}
      </p>
    </div>
  );
}

function HubSpotOneClick({ c, cloud }: { c: Connector; cloud: CloudStatus | null }) {
  const { t } = useI18n();
  const [access, setAccess] = useState<"read" | "write">("read");
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setError(null);
    const res = await connectManaged(c.name, { access });
    if (res.ok) setWaiting(true);
    else setError(res.error || "could not start the connect");
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <p className="text-[13px] text-muted">
        {t("addConn.hubspotConsent")}
      </p>
      <div className="space-y-1.5" data-testid="hubspot-access">
        {(
          [
            ["read", "Read-only", "search and read contacts, companies, deals, tickets"],
            ["write", "Read & write", "adds: log notes and tasks, update records, create contacts — never delete"],
          ] as const
        ).map(([value, label, blurb]) => (
          <label key={value} className="flex items-start gap-2 text-[13px] cursor-pointer">
            <input
              type="radio"
              name="hubspot-access"
              className="mt-0.5"
              checked={access === value}
              data-testid={`hubspot-access-${value}`}
              onChange={() => setAccess(value)}
            />
            <span>
              <span className="font-medium">{label}</span>
              <span className="block text-[12px] text-muted">{blurb}</span>
            </span>
          </label>
        ))}
      </div>
      {cloud?.signed_in ? (
        <button className={PILL_ACCENT + " w-full !py-2"} data-testid="modal-connect-hubspot" onClick={go} disabled={waiting}>
          {waiting ? t("mt.checkBrowser") : t("addConn.connectHubspot")}
        </button>
      ) : cloud ? (
        <CloudSignInInline />
      ) : (
        <CloudStatusPending />
      )}
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-faint text-center">
        {t("addConn.hubspotAnyPortals")}
      </p>
    </div>
  );
}

function SlackManual({ onConnected }: { onConnected: () => void }) {
  const { t } = useI18n();
  const [bot, setBot] = useState("");
  const [app, setApp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setBusy(true);
    setError(null);
    const res = await connectConnector("slack", { bot_token: bot.trim(), app_token: app.trim() });
    setBusy(false);
    if (res.ok) onConnected();
    else setError(res.error || "could not connect");
  };
  return (
    <div className="px-5 py-4 space-y-3">
      <ol className="list-decimal pl-4 text-[13px] text-muted space-y-1">
        <li>{t("addConn.slackStep1")}</li>
        <li>{t("addConn.slackStep2")}</li>
        <li>{t("addConn.slackStep3")}</li>
      </ol>
      <input className={INPUT} type="password" placeholder={t("addConn.botToken")} value={bot} spellCheck={false} onChange={(e) => setBot(e.target.value)} />
      <input className={INPUT} type="password" placeholder={t("addConn.appToken")} value={app} spellCheck={false} onChange={(e) => setApp(e.target.value)} />
      <button className={PILL_LINE + " w-full !py-2"} onClick={submit} disabled={busy || !bot.trim() || !app.trim()}>
        {busy ? t("mt.validating") : t("mt.connect")}
      </button>
      {error && <div className="text-[12.5px] text-danger">{error}</div>}
      <p className="text-[12px] text-warnInk text-center">
        {t("addConn.oneModeAtATime")}
      </p>
    </div>
  );
}
