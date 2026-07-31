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
# Delimiters pair by length (backreference): ````…```` fences and ``…`` inline spans
# close only on a run as long as the one that opened them.
_CODE_SPAN = re.compile(
    r"(?P<fence>`{3,}).*?(?P=fence)"
    r"|(?P<tick>`{1,2})(?:(?!(?P=tick))[^\n])*?(?P=tick)",
    re.DOTALL,
)

_HEADING = re.compile(r"^ {0,3}#{1,6}\s+(\S.*?)\s*#*\s*$", re.MULTILINE)
_BOLD_ITALIC = re.compile(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*")
_BOLD_STARS = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*")
_BOLD_UNDER = re.compile(r"__(?!\s)(.+?)(?<!\s)__")
_STRIKE = re.compile(r"~~(?!\s)(.+?)(?<!\s)~~")
_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HRULE = re.compile(r"^ {0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*$\n?", re.MULTILINE)
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
