# WhatsApp Output Formatting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Outbound WhatsApp messages render properly in the app — markdown the model emits is converted to WhatsApp's own styling syntax at the send boundary.

**Architecture:** A pure-function converter module (`coworker/connectors/wa_format.py`) called once inside `send_whatsapp()` — the single choke point all outbound WhatsApp text passes through. A one-sentence style hint is added to the Assistant persona so the model writes chat-shaped output in the first place.

**Tech Stack:** Python stdlib `re` only. Tests with pytest + monkeypatch (existing idiom in `tests/test_whatsapp.py`).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-31-whatsapp-formatting-design.md`.
- Commits: imperative English sentence, no `feat:` prefix, no Claude coauthor (repo rule).
- Code spans (``` fences and inline backticks) must pass through byte-for-byte.
- `to_whatsapp` must be idempotent — WhatsApp-native input comes back unchanged.
- No new dependencies.

---

### Task 1: Converter module `wa_format.py`

**Files:**
- Create: `coworker/connectors/wa_format.py`
- Test: `tests/test_wa_format.py`

**Interfaces:**
- Produces: `to_whatsapp(text: str) -> str` — imported by Task 2 as `from .wa_format import to_whatsapp`.

- [ ] **Step 1: Write the failing tests**

```python
"""Markdown → WhatsApp conversion. WhatsApp renders single-asterisk bold, underscore
italic, single-tilde strike, backtick code, lists and quotes — NOT headings, double
markers, links or tables. `to_whatsapp` rewrites what maps and neutralises what doesn't."""

from coworker.connectors.wa_format import to_whatsapp


def test_headings_become_bold_lines():
    assert to_whatsapp("### Aguardando sua aprovação:") == "*Aguardando sua aprovação:*"
    assert to_whatsapp("# Top\nbody\n###### Deep") == "*Top*\nbody\n*Deep*"
    assert to_whatsapp("## Closed ##") == "*Closed*"


def test_double_markers_become_single():
    assert to_whatsapp("**bold** and __also bold__") == "*bold* and *also bold*"
    assert to_whatsapp("~~gone~~") == "~gone~"
    assert to_whatsapp("***both***") == "*_both_*"


def test_links_flatten_to_text_and_url():
    assert to_whatsapp("[docs](https://x.dev/a)") == "docs (https://x.dev/a)"
    assert (
        to_whatsapp("[https://x.dev/a](https://x.dev/a)") == "https://x.dev/a"
    ), "self-link keeps just the url"
    assert to_whatsapp("![diagram](https://x.dev/i.png)") == "https://x.dev/i.png"


def test_horizontal_rules_are_dropped():
    assert to_whatsapp("above\n---\nbelow") == "above\nbelow"
    assert to_whatsapp("a\n***\nb\n___\nc") == "a\nb\nc"


def test_lists_and_quotes_pass_through():
    text = "- item\n* other\n1. first\n> quoted"
    assert to_whatsapp(text) == text


def test_code_spans_are_untouched():
    fenced = "```\n### not a heading\n**not bold**\n```"
    assert to_whatsapp(fenced) == fenced
    assert to_whatsapp("run `git log --format='**'` now") == "run `git log --format='**'` now"


def test_tables_become_monospace_blocks():
    table = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert to_whatsapp(table) == "```\n| a | b |\n| 1 | 2 |\n```"


def test_idempotent_on_whatsapp_native_text():
    native = "*bold* _it_ ~s~ `code`\n- item\n> quote"
    assert to_whatsapp(native) == native
    assert to_whatsapp(to_whatsapp("### H\n**b** [t](https://u.dev)")) == to_whatsapp(
        "### H\n**b** [t](https://u.dev)"
    )


def test_real_world_session_listing():
    raw = (
        "Você tem *6 sessões ativas*:\n\n"
        "### Aguardando sua aprovação (waiting_approval):\n"
        "1. *nexttrace* – TTY: ttys000\n"
    )
    got = to_whatsapp(raw)
    assert "###" not in got
    assert "*Aguardando sua aprovação (waiting_approval):*" in got


def test_empty_and_plain_text():
    assert to_whatsapp("") == ""
    assert to_whatsapp("oi, tudo bem?") == "oi, tudo bem?"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_wa_format.py -v`
Expected: FAIL — `ModuleNotFoundError: coworker.connectors.wa_format`

- [ ] **Step 3: Write the implementation**

```python
"""Markdown → WhatsApp text conversion.

WhatsApp renders its own styling — single-asterisk bold, underscore italic,
single-tilde strikethrough, backtick code, `-`/`*`/`1.` lists, `>` quotes — and
shows everything else (ATX headings, `**double markers**`, `[text](url)`,
tables, horizontal rules) as literal characters. Agent replies arrive as
GitHub-flavored markdown, so `to_whatsapp` rewrites the constructs that map and
flattens the ones that don't. Code spans pass through byte-for-byte, and
WhatsApp-native input comes back unchanged (the function is idempotent).
"""

from __future__ import annotations

import re

# Fenced blocks before inline spans so a backtick inside a fence can't split it.
_CODE_SPAN = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)

_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(\S.*?)\s*#*\s*$", re.MULTILINE)
_BOLD_ITALIC = re.compile(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*")
_BOLD_STARS = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*")
_BOLD_UNDER = re.compile(r"__(?!\s)(.+?)(?<!\s)__")
_STRIKE = re.compile(r"~~(?!\s)(.+?)(?<!\s)~~")
_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HRULE = re.compile(r"^ {0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*\n?", re.MULTILINE)
_TABLE_SEP = re.compile(r"\s*\|?[\s:|-]*-[\s:|-]*")


def to_whatsapp(text: str) -> str:
    """Rewrite markdown `text` into WhatsApp styling; code spans stay untouched."""
    if not text:
        return text
    pieces: list[str] = []
    last = 0
    for span in _CODE_SPAN.finditer(text):
        pieces.append(_convert(text[last : span.start()]))
        pieces.append(span.group(0))
        last = span.end()
    pieces.append(_convert(text[last:]))
    return "".join(pieces)


def _flatten_link(match: re.Match[str]) -> str:
    label, url = match.group(1), match.group(2)
    return url if label.strip() == url else f"{label} ({url})"


def _convert(chunk: str) -> str:
    chunk = _HEADING.sub(lambda m: f"*{m.group(1)}*", chunk)
    chunk = _HRULE.sub("", chunk)
    chunk = _BOLD_ITALIC.sub(r"*_\1_*", chunk)
    chunk = _BOLD_STARS.sub(r"*\1*", chunk)
    chunk = _BOLD_UNDER.sub(r"*\1*", chunk)
    chunk = _STRIKE.sub(r"~\1~", chunk)
    chunk = _IMAGE.sub(r"\1", chunk)
    chunk = _LINK.sub(_flatten_link, chunk)
    return _tables_to_monospace(chunk)


def _tables_to_monospace(chunk: str) -> str:
    """WhatsApp has no tables; a run of `|`-rows at least two lines long becomes a
    monospace block (alignment survives), with the `|---|` separator row dropped."""
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if len(run) >= 2:
            rows = [r for r in run if not ("-" in r and _TABLE_SEP.fullmatch(r))]
            out.append("```\n" + "\n".join(rows) + "\n```")
        else:
            out.extend(run)
        run.clear()

    for line in chunk.split("\n"):
        if line.lstrip().startswith("|"):
            run.append(line)
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_wa_format.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add coworker/connectors/wa_format.py tests/test_wa_format.py
git commit -m "Add markdown-to-WhatsApp text converter"
```

---

### Task 2: Wire converter into `send_whatsapp`

**Files:**
- Modify: `coworker/connectors/whatsapp.py` (function `send_whatsapp`, ~line 285)
- Test: `tests/test_whatsapp.py`

**Interfaces:**
- Consumes: `to_whatsapp(text: str) -> str` from Task 1.
- Produces: unchanged `send_whatsapp` signature; behavior change only (text converted before POST).

- [ ] **Step 1: Write the failing test** (append to `tests/test_whatsapp.py`, reusing its `_Resp` helper)

```python
def test_send_converts_markdown_to_whatsapp_styling(monkeypatch):
    """Model output is GitHub markdown; Evolution delivers text verbatim, so the
    conversion has to happen here — the one choke point every send passes through."""
    seen: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append({"json": json})
        return _Resp(201, {"key": {"id": "WAMID9"}})

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)

    send_whatsapp(
        "http://x:8090", "KEY", "openworker",
        "5511999999999@s.whatsapp.net", "### Resumo\n**pronto**",
    )
    assert seen[0]["json"]["text"] == "*Resumo*\n*pronto*"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_whatsapp.py::test_send_converts_markdown_to_whatsapp_styling -v`
Expected: FAIL — text posted verbatim (`### Resumo\n**pronto**`)

- [ ] **Step 3: Implement** — in `send_whatsapp()` in `coworker/connectors/whatsapp.py`, convert before building the payload:

```python
def send_whatsapp(
    base_url: str, api_key: str, instance: str, chat_id: str, text: str
) -> SendResult:
    """One-shot outbound send. Sync, like the other senders — the engine runs it in a thread."""
    import httpx

    from .wa_format import to_whatsapp

    number = jid_to_number(chat_id)
    if not number:
        return SendResult(False, error="empty WhatsApp recipient")
    # The model writes markdown; WhatsApp renders its own syntax. Convert at the one
    # point every outbound text passes through.
    text = to_whatsapp(text)
    ...  # rest of the function unchanged
```

- [ ] **Step 4: Run the whole WhatsApp suite**

Run: `source .venv/bin/activate && pytest tests/test_whatsapp.py tests/test_wa_format.py -v`
Expected: all PASS (existing sends use plain "hi" — unaffected)

- [ ] **Step 5: Commit**

```bash
git add coworker/connectors/whatsapp.py tests/test_whatsapp.py
git commit -m "Convert outbound WhatsApp text from markdown at the send boundary"
```

---

### Task 3: Persona style hint

**Files:**
- Modify: `coworker/agents/assistant.py` (`ASSISTANT_INSTRUCTIONS`, after "reply in the language the message was written in.")

**Interfaces:**
- Consumes: nothing from other tasks (independent).
- Produces: prompt text only; no code interface.

- [ ] **Step 1: Edit the instruction string** — insert after the sentence "Either way, reply in the language the message was written in. ":

```python
    "Chat platforms render only simple styling: write chat-shaped replies — short "
    "lines, *bold* labels instead of markdown headings, no tables, no [text](url) "
    "links. "
```

(The converter at the send boundary guarantees syntax; this shapes structure, which
no converter can retrofit.)

- [ ] **Step 2: Run the full Python suite**

Run: `source .venv/bin/activate && pytest`
Expected: all pass (~1241 tests; no test pins the instruction text)

- [ ] **Step 3: Commit**

```bash
git add coworker/agents/assistant.py
git commit -m "Tell the assistant persona to write chat-shaped replies"
```

---

## Self-review

- Spec coverage: converter rules 1–7 → Task 1 tests; choke-point wiring → Task 2; persona hint → Task 3. Non-goals untouched. ✓
- No placeholders; all code inline. ✓
- Type consistency: `to_whatsapp(text: str) -> str` used identically in Tasks 1–2. ✓
