"""Read and watch a Claude Code session transcript (~/.claude/projects/*/*.jsonl).

Each line is one JSON object. Only `type: user|assistant` lines with visible text count
as entries; mode markers, snapshots, sidechain (subagent) lines, thinking-only and
tool-result-only lines are skipped. A truncated line (a write in progress) is skipped
too — parsing never raises. Files are re-read whole on each call: transcripts are a few
MB at worst and this keeps the reader stateless.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


@dataclass
class Entry:
    role: str  # "user" | "assistant"
    text: str
    timestamp: datetime | None


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A timestamp without an offset parses naive; comparing that against the aware
    # `after` in wait_for_reply would raise TypeError. Real transcripts are UTC.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _block_text(content: object) -> str:
    """Visible text of a message: user content is a string, assistant content a block
    list where only `text` blocks are human-directed (thinking/tool_use are not)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def parse_line(line: str) -> Entry | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("isSidechain"):
        return None
    kind = data.get("type")
    if kind not in ("user", "assistant"):
        return None
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    text = _block_text(message.get("content"))
    if not text.strip():
        return None
    return Entry(role=kind, text=text, timestamp=_parse_timestamp(data.get("timestamp")))


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def tail(path: Path, n: int = 20) -> list[Entry]:
    entries = [e for e in (parse_line(line) for line in _lines(path)) if e]
    return entries[-n:]


def last_branch(path: Path) -> str | None:
    """The session's git branch, from the newest line that recorded one. Sidechain
    (subagent) lines are skipped — a subagent may run elsewhere."""
    branch: str | None = None
    for line in _lines(path):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(data, dict)
            and not data.get("isSidechain")
            and isinstance(data.get("gitBranch"), str)
        ):
            branch = data["gitBranch"]
    return branch


def wait_for_reply(
    path: Path,
    after: datetime,
    *,
    timeout: float = 120.0,
    poll: float = 1.0,
    settle: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str | None:
    """Assistant text newer than `after`, once the transcript has been quiet.

    A turn can emit several assistant entries (narration between tool calls), so a reply
    only counts as final after `settle` seconds with no new entry. Quiet is judged on a
    snapshot of the entries, not their count — once the 50-entry window fills, a new
    message shifts the window without changing the count. On timeout, whatever arrived
    is returned anyway (partial beats silence); None means nothing arrived.
    """
    deadline = clock() + timeout
    quiet_since: float | None = None
    prev: list[tuple[datetime | None, str]] | None = None
    texts: list[str] = []
    while True:
        fresh = [
            e
            for e in tail(path, 50)
            if e.role == "assistant" and e.timestamp and e.timestamp > after
        ]
        texts = [e.text for e in fresh]
        snapshot = [(e.timestamp, e.text) for e in fresh]
        if snapshot:
            if snapshot != prev:
                prev = snapshot
                quiet_since = clock()
            elif quiet_since is not None and clock() - quiet_since >= settle:
                return "\n\n".join(texts)
        if clock() >= deadline:
            return "\n\n".join(texts) if texts else None
        sleep(poll)
