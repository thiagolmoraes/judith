# WhatsApp output formatting — design

Date: 2026-07-31
Status: approved (autonomous flow — direct user directive)

## Problem

Agent replies delivered over WhatsApp arrive as GitHub-flavored markdown. WhatsApp
renders only its own syntax, so `### Heading`, `**bold**`, `[text](url)`, `~~strike~~`
and `---` show up as literal characters in the chat. Observed in production: a session
listing sent to the user rendered `### Aguardando sua aprovação:` verbatim.

WhatsApp's supported syntax (per WhatsApp Help Center / formatting guides, 2024+):

| Style          | Syntax                  |
| -------------- | ----------------------- |
| Bold           | `*text*` (single `*`)   |
| Italic         | `_text_`                |
| Strikethrough  | `~text~` (single `~`)   |
| Monospace block| ` ```text``` `          |
| Inline code    | `` `text` ``            |
| Bulleted list  | `* item` or `- item`    |
| Numbered list  | `1. item`               |
| Quote          | `> text`                |

Not supported: `#` headings of any level, `[text](url)` links, tables, horizontal
rules, `**double-asterisk**` bold. Monospace can't combine with other styles.

## Decision

Deterministic markdown→WhatsApp converter applied at the send boundary, plus a
one-line style hint in the Assistant persona.

- **New module** `coworker/connectors/wa_format.py` exposing `to_whatsapp(text: str) -> str`.
- **Single call site**: inside `send_whatsapp()` (`coworker/connectors/whatsapp.py`),
  the one choke point every outbound WhatsApp text passes through — the `send_message`
  tool (via `senders.py`), `EvolutionConnector.send`, and bridge notifications alike.
- **Persona hint**: one sentence in `ASSISTANT_INSTRUCTIONS` telling the model that
  chat platforms render simple styling only — write chat-style (short lines, `*bold*`
  labels, no headings/tables). The converter guarantees syntax; the hint improves
  structure, which no regex can retrofit.

### Rejected alternatives

- **Prompt-only**: the model demonstrably slips (the bug report is one such slip);
  every persona and future agent would need the same paragraph.
- **Converter in each sender/tool**: N call sites to keep in sync; `send_whatsapp()`
  already funnels everything.

## Conversion rules

Applied only outside code spans — fenced blocks (```` ``` ````) and inline backticks
pass through byte-for-byte:

1. ATX headings `#`–`######` → bold line (`### Título` → `*Título*`).
2. `**bold**` and `__bold__` → `*bold*`; `***both***` → `*_both_*`.
3. `~~strike~~` → `~strike~`.
4. `[text](url)` → `text (url)`; when text equals the url, just the url.
   Images `![alt](url)` → url.
5. Horizontal rules (`---` / `***` / `___` alone on a line) → line removed.
6. Markdown tables (contiguous `|`-delimited lines) → wrapped in a monospace block.
7. Lists (`- `, `* `, `1. `) and quotes (`> `) already native — unchanged.

`to_whatsapp` is idempotent: WhatsApp-native input comes back unchanged.

## Non-goals

- Telegram plain-text sends have the same raw-markdown problem — separate change.
- No renumbering/reflow of the model's prose; structure fixes come from the persona hint.

## Testing

`tests/test_wa_format.py`, pure unit tests over `to_whatsapp`: each rule above, code-span
preservation, idempotency on already-converted text, and the real-world sample from the
bug report (session listing with `###` headers). One integration assertion in
`tests/test_whatsapp.py`: `send_whatsapp` posts converted text to the Evolution API.
