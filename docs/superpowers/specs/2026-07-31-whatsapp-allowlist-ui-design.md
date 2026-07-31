# WhatsApp allow-list UI — design (phase 1)

Date: 2026-07-31
Status: approved (decisions taken with the owner in session)

## Problem

WhatsApp has no bespoke connector page: it falls through to `GenericDetail`
(`surfaces/gui/src/components/connectors/ConnectorsSection.tsx:120`), which renders the
shared `AllowlistBlock` + `UnauthorizedBlock`. Those already cover a lot — parked
messages from unauthorized senders persist in `parked.json` (cap 100) and can be
resolved with allow / allow-and-deliver / dismiss.

What is missing is the ability to authorize someone who has **not messaged yet**:

1. **No way to add a number by hand.** The allow-list is read-only except for the
   one-click Allow on a sender who already wrote in. To authorize a person up front —
   a family member, a supplier — there is no path at all.
2. **The contact list is never consulted.** Evolution exposes
   `POST /chat/findContacts/{instance}`, but the connector only calls sendText, the
   webhook registration, and connection state. The owner must know the raw number.
3. **No WhatsApp context in the UI.** Identifiers render as bare ids
   (`5511999999999`) with no phone formatting and nothing saying these are phone
   numbers.

## Decision

A bespoke `WhatsAppDetail` page that reuses the existing blocks and adds one new
block — "Add someone" — with two ways in: type a number, or search the WhatsApp
contact list. Backend gains a contact-directory abstraction and phone
normalization; **no new allow-list write path** (the existing
`POST /v1/connectors/{name}/allow` already takes `user_id` + display `name`).

### Backend units (one responsibility each)

| Unit | File | Responsibility |
| --- | --- | --- |
| `normalize_phone` / `format_phone` | `coworker/connectors/phone.py` (new) | Pure string functions: digits-only E.164-ish normalization for storage, grouped display for the UI. No I/O. |
| `Contact`, `ContactDirectory` | `coworker/connectors/contacts.py` (new) | The abstraction consumers depend on: `search(query, limit) -> list[Contact]`. A `Protocol`, so the server never imports Evolution. |
| `EvolutionContactDirectory` | `coworker/connectors/whatsapp.py` | The only place that knows `POST /chat/findContacts/{instance}` exists — same containment rule the module header already states for the rest of Evolution. |
| `GET /v1/connectors/whatsapp_evolution/contacts` | `coworker/server/app.py` | Thin route: resolve the configured directory, delegate, return `{contacts: [...]}`. Never talks HTTP itself. |

`ContactDirectory` is what the route depends on (dependency inversion): swapping
Evolution for WAHA is a new implementation, not an edit to the route or the GUI. The
manager exposes `contact_directory()` so tests inject a fake with zero network.

### Search semantics

- Query matches contact **name** (case-insensitive substring) or **number**
  (digits-only substring), so "ana" and "9999" both work.
- Results capped (default 20) — the response is a picker, not a dump.
- Contacts are read on demand and returned to the caller. **Nothing is persisted**:
  no contact file, no cache on disk. The only thing that ever gets written is the
  number the owner explicitly authorizes (into the existing allow-list) plus the
  display name it already stores in `people.json`.
- Groups (`@g.us`) and the owner's own number are filtered out — neither is a
  person to authorize.
- Anyone already on the allow-list is marked `allowed: true` so the UI can show
  "Added" instead of offering a duplicate.

### Failure behavior

An unreachable or unsupported Evolution instance returns
`{"ok": false, "error": ...}` with an empty list — the page must still render, and
typing a number by hand must keep working when search is unavailable. The GUI shows
the error next to the search box, never an empty state that implies "no contacts".

### GUI

`WhatsAppDetail` registered in `DETAIL_PAGES` for `whatsapp_evolution`:

1. Status header + Disconnect (as today).
2. **Add someone** (new): a number field (normalized on submit; invalid input is
   rejected inline with a reason) and a search field over the contact list, each
   result row carrying an Add button.
3. `AllowlistBlock` + `UnauthorizedBlock` + `ConnectorTools` — unchanged components,
   reused as-is; numbers render formatted through `formatPhone`.

All copy through i18n (en + pt-BR).

## Non-goals (deferred to phase 2)

- Per-person permission tiers (owner / trusted / restricted) and the connector-wide
  tool ceiling. That touches the permission path and gets its own PR.
- Contact search for other connectors.

## Testing

- Python: `normalize_phone` / `format_phone` table tests (BR mobile with and without
  country code, punctuation, garbage, already-normalized input); `EvolutionContactDirectory`
  against a fake HTTP client (payload shape variations, groups filtered, own number
  filtered, cap honored, network failure → error result); the route with an injected
  fake directory (including the `allowed` flag and the failure envelope).
- GUI: `WhatsAppDetail` renders the WhatsApp page for `whatsapp_evolution`; adding a
  typed number posts the normalized value to `/allow`; invalid input shows the reason
  and posts nothing; a search result's Add posts number + name; a failed search shows
  the error, not an empty state. i18n guard stays green.
