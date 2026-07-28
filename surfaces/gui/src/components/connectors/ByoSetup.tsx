import { useEffect, useState } from "react";
import {
  byoConnect,
  getByoStatus,
  setByoConfig,
  type ByoStatus,
  type Connector,
} from "../../api";
import { PILL_ACCENT, PILL_LINE, TAG_QUIET } from "./ui";
import { useI18n } from "../../i18n/useLocale";

// The third connect mode: browser consent driven by an OAuth app the USER registered,
// so one-click works with no OpenWorker Cloud sign-in and the agent acts as their app.
// Setup is a one-time paste of the app's credentials; after that it's the same
// click-and-approve as the managed path. Registering the app is the cost — this pane
// is deliberately explicit about that rather than hiding it behind a button that fails.

const INPUT =
  "w-full px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent";
const LABEL = "text-[12px] font-medium text-muted";

/** Where the user registers an app, and what they'll get back. Keeping the exact
 * callback URL here matters: providers reject a redirect that doesn't match. */
const GUIDES: Record<
  string,
  { href: string; where: string; yields: string; extra?: string }
> = {
  github: {
    href: "https://github.com/settings/apps/new",
    where: "GitHub → Settings → Developer settings → GitHub Apps → New",
    yields: "an App ID and a generated private key (.pem)",
    extra: "Grant it the repository permissions you want the agent to have.",
  },
  gmail: {
    href: "https://console.cloud.google.com/apis/credentials",
    where: "Google Cloud Console → APIs & Services → Credentials → OAuth client ID",
    yields: "a client ID and client secret",
    extra: "Enable the Gmail API for the project, and add yourself as a test user.",
  },
  google_calendar: {
    href: "https://console.cloud.google.com/apis/credentials",
    where: "Google Cloud Console → APIs & Services → Credentials → OAuth client ID",
    yields: "a client ID and client secret",
    extra: "Enable the Calendar API for the project.",
  },
  google_drive: {
    href: "https://console.cloud.google.com/apis/credentials",
    where: "Google Cloud Console → APIs & Services → Credentials → OAuth client ID",
    yields: "a client ID and client secret",
    extra: "Enable the Drive API for the project.",
  },
  notion: {
    href: "https://www.notion.so/my-integrations",
    where: "Notion → My integrations → New public integration",
    yields: "an OAuth client ID and secret",
  },
  slack: {
    href: "https://api.slack.com/apps",
    where: "Slack → Your Apps → Create New App",
    yields: "a client ID and client secret",
  },
  outlook: {
    href: "https://entra.microsoft.com",
    where: "Microsoft Entra → App registrations → New registration",
    yields: "an application (client) ID and a client secret",
  },
  hubspot: {
    href: "https://developers.hubspot.com/",
    where: "HubSpot → Developer account → Apps → Create app",
    yields: "a client ID and client secret",
  },
  attio: {
    href: "https://developers.attio.com/",
    where: "Attio → Developers → Integrations → New",
    yields: "a client ID and client secret",
  },
};

export function byoSupported(name: string): boolean {
  return name in GUIDES;
}

export function ByoSetup({
  c,
  onConnected,
}: {
  c: Connector;
  onConnected: () => void;
}) {
  const { t } = useI18n();
  const [status, setStatus] = useState<ByoStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const isGithub = c.name === "github";

  // GitHub takes an App id + PEM; every other provider takes client id + secret.
  const [appId, setAppId] = useState("");
  const [privateKey, setPrivateKey] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [scopes, setScopes] = useState("");

  // `status === null` alone can't say whether the fetch is in flight or failed — treating
  // both as "loading" leaves a network error stuck on "Checking…" with no way out.
  const [loadFailed, setLoadFailed] = useState(false);
  const refresh = async () => {
    setLoadFailed(false);
    try {
      setStatus(await getByoStatus());
    } catch {
      setStatus(null);
      setLoadFailed(true);
    }
  };
  useEffect(() => {
    void refresh();
  }, []);

  const guide = GUIDES[c.name];
  const configured = isGithub
    ? !!status?.github.configured
    : !!status?.oauth[c.name]?.configured;
  // Show the form when nothing is stored yet, or when the user chose to change it.
  const showForm = editing || (status !== null && !configured);

  const save = async () => {
    setError(null);
    setBusy(true);
    try {
      const fields: Record<string, string> = isGithub
        ? { app_id: appId.trim(), private_key: privateKey.trim() }
        : {
            client_id: clientId.trim(),
            client_secret: clientSecret.trim(),
            ...(scopes.trim() ? { scopes: scopes.trim() } : {}),
          };
      const res = await setByoConfig(c.name, fields);
      if (!res.ok) {
        setError(res.error || "could not save those credentials");
        return;
      }
      setPrivateKey("");
      setClientSecret("");
      setEditing(false);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  const connect = async () => {
    setError(null);
    setBusy(true);
    try {
      const res = await byoConnect(c.name);
      if (!res.ok) setError(res.error || "could not start the connect");
      else onConnected();
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    setError(null);
    setBusy(true);
    try {
      const res = await setByoConfig(c.name, isGithub ? { app_id: "" } : { client_id: "" });
      if (!res.ok) {
        // Same contract as save(): a refused write must say so rather than closing the
        // form and leaving the app still configured with no explanation.
        setError(res.error || "could not remove that app");
        return;
      }
      setEditing(false);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  if (!guide) {
    return (
      <div className="px-5 py-4 text-[13px] text-muted">
        {c.title} has no bring-your-own app path.
      </div>
    );
  }

  return (
    <div className="px-5 py-4 space-y-3">
      <p className="text-[13px] text-muted">
        Use your own {isGithub ? "GitHub App" : "OAuth app"} instead of OpenWorker's. Same
        click-and-approve, no cloud sign-in, and the agent acts as your app.
      </p>

      {loadFailed ? (
        <div className="rounded-lg border border-line bg-paper px-3 py-2.5 flex items-center gap-3">
          <span className="text-[12.5px] text-muted flex-1">
            {t("byo.couldNotRead")}
          </span>
          <button
            className={PILL_LINE}
            onClick={() => void refresh()}
            data-testid="byo-retry"
          >
            {t("byo.retry")}
          </button>
        </div>
      ) : status === null ? (
        <div className="text-[12px] text-faint py-2 text-center">{t("byo.checking")}</div>
      ) : showForm ? (
        <>
          <div className="rounded-lg border border-line bg-paper px-3 py-2.5 space-y-1">
            <div className="text-[12px] text-muted">
              Register one at{" "}
              <a
                className="text-accent hover:underline"
                href={guide.href}
                target="_blank"
                rel="noreferrer"
              >
                {guide.where}
              </a>
              . You'll get {guide.yields}.
            </div>
            {guide.extra && <div className="text-[11.5px] text-faint">{guide.extra}</div>}
            {!isGithub && (
              <div className="text-[11.5px] text-faint">
                Set the redirect URI to{" "}
                <code className="text-ink" data-testid="byo-redirect-uri">
                  {status.redirect_uri}
                </code>{" "}
                — the provider rejects anything that doesn't match exactly.
              </div>
            )}
          </div>

          {isGithub ? (
            <>
              <label className="block space-y-1">
                <span className={LABEL}>{t("byo.appId")}</span>
                <input
                  className={INPUT}
                  value={appId}
                  onChange={(e) => setAppId(e.target.value)}
                  placeholder="123456"
                  data-testid="byo-app-id"
                />
              </label>
              <label className="block space-y-1">
                <span className={LABEL}>Private key (.pem contents)</span>
                <textarea
                  className={INPUT + " font-mono text-[11px] h-24 resize-y"}
                  value={privateKey}
                  onChange={(e) => setPrivateKey(e.target.value)}
                  placeholder={t("byo.privateKeyPlaceholder")}
                  data-testid="byo-private-key"
                />
              </label>
            </>
          ) : (
            <>
              <label className="block space-y-1">
                <span className={LABEL}>{t("byo.clientId")}</span>
                <input
                  className={INPUT}
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  data-testid="byo-client-id"
                />
              </label>
              <label className="block space-y-1">
                <span className={LABEL}>{t("byo.clientSecret")}</span>
                <input
                  className={INPUT}
                  type="password"
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                  placeholder={configured ? "•••••• (leave blank to keep)" : ""}
                  data-testid="byo-client-secret"
                />
              </label>
              <label className="block space-y-1">
                <span className={LABEL}>Scopes (optional)</span>
                <input
                  className={INPUT}
                  value={scopes}
                  onChange={(e) => setScopes(e.target.value)}
                  placeholder={t("byo.scopesPlaceholder")}
                  data-testid="byo-scopes"
                />
              </label>
            </>
          )}

          <div className="flex gap-2">
            <button
              className={PILL_ACCENT + " flex-1 !py-2"}
              onClick={() => void save()}
              disabled={busy || (isGithub ? !appId.trim() : !clientId.trim())}
              data-testid="byo-save"
            >
              {busy ? "Saving…" : "Save app"}
            </button>
            {configured && (
              <button className={PILL_LINE} onClick={() => setEditing(false)} disabled={busy}>
                {t("byo.cancel")}
              </button>
            )}
          </div>
        </>
      ) : (
        <>
          <div className="rounded-lg border border-line bg-paper px-3 py-2.5 flex items-center gap-2">
            <span className={TAG_QUIET}>{t("byo.yourApp")}</span>
            <span className="text-[12.5px] text-ink flex-1 truncate">
              {isGithub ? `App ID ${status.github.app_id}` : status.oauth[c.name]?.client_id}
            </span>
            <button
              className="text-[12px] text-accent hover:underline shrink-0"
              onClick={() => {
                // Prefill from what's stored: the identifier is on screen right above,
                // and making the user retype it from memory just to rotate a secret is
                // needless friction. The secret itself stays blank — blank means "keep".
                if (isGithub) setAppId(status.github.app_id);
                else {
                  setClientId(status.oauth[c.name]?.client_id ?? "");
                  setScopes((status.oauth[c.name]?.scopes ?? []).join(" "));
                }
                setEditing(true);
              }}
            >
              {t("byo.change")}
            </button>
            <button
              className="text-[12px] text-faint hover:text-danger shrink-0"
              onClick={() => void clear()}
              disabled={busy}
            >
              {t("byo.remove")}
            </button>
          </div>
          <button
            className={PILL_ACCENT + " w-full !py-2"}
            onClick={() => void connect()}
            disabled={busy}
            data-testid="byo-connect"
          >
            {busy ? "Check your browser…" : `Connect ${c.title}`}
          </button>
          {isGithub && (
            <p className="text-[11.5px] text-faint text-center">
              {t("byo.installHint")}
            </p>
          )}
        </>
      )}

      {error && <div className="text-[12.5px] text-danger">{error}</div>}
    </div>
  );
}
