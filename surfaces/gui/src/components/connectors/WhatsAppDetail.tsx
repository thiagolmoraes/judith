import { useEffect, useRef, useState } from "react";
import { allowUser, disconnectConnector, searchContacts, type ContactRow } from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { AllowlistBlock, ConnectorTools, ListeningSessionsBlock, UnauthorizedBlock } from "../ManageTabs";
import type { DetailProps } from "./ConnectorsSection";
import { normalizePhone } from "../../phone";
import { GRP, PILL_ACCENT } from "./ui";
import { useI18n } from "../../i18n/useLocale";

// Matches ManageTabs' block headers (which keep this class private to that module) so
// "Add someone" sits flush with "Allowed to message" directly below it.
const SEC_H = "text-[11px] uppercase tracking-[0.05em] text-faint font-semibold";

// The WhatsApp detail page: GenericDetail's shape plus the one thing WhatsApp needs and
// no other connector has — a way to authorize someone who has NEVER written in. Everywhere
// else the allow-list grows from recent senders, but on WhatsApp the person you most want
// to reach is the one who hasn't messaged yet, and their id is a phone number you know by
// heart or a name in the phone's address book. Hence two paths into one block: type it, or
// find it. Both end at the same POST /allow the recent-sender chips use.

/** Add-by-hand + contact search. Sits ABOVE the allow-list because it is the reason the
 * owner opened this page; the list below is the result. */
function AddSomeoneBlock({ c, onChanged }: { c: DetailProps["c"]; onChanged: () => void }) {
  const { t } = useI18n();
  const [typed, setTyped] = useState("");
  const [invalid, setInvalid] = useState(false);
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<ContactRow[]>([]);
  const [searchError, setSearchError] = useState<string | null>(null);
  // Rows the owner just added: the allow-list refresh is a poll away, and a button that
  // stays "Add" after a successful add reads as a failure.
  const [justAdded, setJustAdded] = useState<string[]>([]);
  // Only the newest search may write state — responses can land out of order, and an
  // older, slower one would repaint the list with results for a query already replaced.
  const seq = useRef(0);
  // `t` via a ref, deliberately: used outside the I18nProvider `useI18n` returns a fresh
  // object each render, so a `t` in the dep array below re-runs the effect on every
  // render it causes — an infinite loop that hangs rather than fails.
  const tr = useRef(t);
  tr.current = t;

  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setRows([]);
      setSearchError(null);
      return;
    }
    // Debounced: a request per keystroke would hammer the directory for prefixes the
    // owner is still in the middle of typing.
    const mine = ++seq.current;
    const timer = setTimeout(async () => {
      try {
        const res = await searchContacts(c.name, q);
        if (mine !== seq.current) return;
        if (res.ok) {
          setRows(res.contacts ?? []);
          setSearchError(null);
        } else {
          // An empty list here would claim "nobody by that name" when the truth is that
          // lookup never ran. Say so, and leave the type-a-number path working.
          setRows([]);
          setSearchError(res.error || tr.current("wa.searchUnavailable"));
        }
      } catch {
        if (mine !== seq.current) return;
        setRows([]);
        setSearchError(tr.current("wa.searchUnavailable"));
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [query, c.name]);

  const addTyped = async () => {
    // Normalized client-side to the exact key a webhook produces — an unnormalized number
    // would sit on the allow-list matching nobody.
    const number = normalizePhone(typed);
    if (!number) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    await allowUser(c.name, number);
    setTyped("");
    onChanged();
  };

  const addContact = async (row: ContactRow) => {
    await allowUser(c.name, row.number, undefined, row.name ?? undefined);
    setJustAdded((prev) => [...prev, row.number]);
    onChanged();
  };

  return (
    <div className="px-3.5 py-3" data-testid="wa-add-someone">
      <div className={SEC_H + " mb-2"}>{t("wa.addSomeone")}</div>

      <div className="flex items-center gap-2">
        <input
          className="flex-1 text-[12.5px] px-2.5 py-1.5 rounded-lg bg-paper border border-line"
          data-testid="wa-number-input"
          placeholder={t("wa.numberPlaceholder")}
          value={typed}
          onChange={(e) => {
            setTyped(e.target.value);
            setInvalid(false); // the reason is about what was submitted, not what is being typed
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") addTyped();
          }}
        />
        <button className={PILL_ACCENT} data-testid="wa-add-btn" onClick={addTyped}>
          {t("wa.add")}
        </button>
      </div>
      {invalid && (
        <div className="text-[12px] text-danger mt-1.5" data-testid="wa-invalid">
          {t("wa.invalidNumber")}
        </div>
      )}

      <div className="mt-3">
        <input
          className="w-full text-[12.5px] px-2.5 py-1.5 rounded-lg bg-paper border border-line"
          data-testid="wa-search-input"
          placeholder={t("wa.searchPlaceholder")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {searchError && (
          <div className="text-[12px] text-warnInk mt-1.5" data-testid="wa-search-error">
            {searchError}
          </div>
        )}
        {rows.length > 0 && (
          <div className="mt-2 space-y-1.5" data-testid="wa-results">
            {rows.map((r) => {
              const added = r.allowed || justAdded.includes(r.number);
              return (
                <div className="flex items-center gap-2 text-[12.5px]" key={r.number}>
                  <span className="min-w-0 truncate">
                    {r.name || t("wa.unknownContact")}{" "}
                    <span className="text-faint">· {r.display}</span>
                  </span>
                  {added ? (
                    <span
                      className="ml-auto text-[11.5px] px-2 py-0.5 text-faint shrink-0"
                      data-testid="wa-added"
                    >
                      {t("wa.added")}
                    </span>
                  ) : (
                    <button
                      className="ml-auto text-[11.5px] px-2 py-0.5 rounded-md bg-accent text-white shrink-0"
                      data-testid="wa-add-contact"
                      onClick={() => addContact(r)}
                    >
                      {t("wa.add")}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

export function WhatsAppDetail({
  c,
  cloud: _cloud,
  slack: _slack,
  onChanged,
  onGone,
}: DetailProps) {
  const { t } = useI18n();
  return (
    <div data-testid="whatsapp-detail">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title={c.title} />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">{c.title}</h2>
          <div className="text-[12.5px] text-muted flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-ok" />
            {c.account || t("wa.connected")}
          </div>
        </div>
        {c.auth !== "none" && (
          <button
            className="text-[12.5px] text-danger/80 hover:text-danger shrink-0"
            onClick={async () => {
              await disconnectConnector(c.name);
              onChanged();
              // Leave the page: this connector is gone, and staying would render a
              // detail view for something that no longer exists.
              onGone?.();
            }}
          >
            {t("conn.disconnect")}
          </button>
        )}
      </div>

      <div className={GRP}>
        <ConnectorTools c={c} onChanged={onChanged} />
      </div>

      {c.two_way && (
        <div className={GRP + " mt-4"}>
          <AddSomeoneBlock c={c} onChanged={onChanged} />
          <AllowlistBlock c={c} onChanged={onChanged} />
          <UnauthorizedBlock c={c} onChanged={onChanged} />
          {c.channels && <ListeningSessionsBlock c={c} />}
        </div>
      )}
    </div>
  );
}
