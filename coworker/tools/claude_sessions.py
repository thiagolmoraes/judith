"""Bridge tools — find live Claude Code CLI sessions, read their transcripts, send them
input. Thin adapter over coworker.claude_bridge (same factory-of-closures shape as
git_tools); all logic lives in the domain package, all deps are injected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol

from ..claude_bridge.discovery import SessionDiscovery
from ..claude_bridge.models import LiveSession
from ..claude_bridge.terminal import ITerm2Driver, TerminalDriver
from ..claude_bridge.transcript import tail as transcript_tail
from ..claude_bridge.transcript import wait_for_reply

# Ceilings on model-supplied numbers: a huge `n` would dump a whole transcript into the
# context; a huge `wait_seconds` would pin the turn on one blocked session.
_MAX_ENTRIES = 100
_MAX_WAIT_SECONDS = 600


class SessionFinder(Protocol):
    """What the tools need from discovery — the injection seam for fakes."""

    def list(self) -> list[LiveSession]: ...

_FIND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_claude_sessions",
        "description": (
            "List the live Claude Code CLI sessions in terminal tabs on this machine: "
            "project, branch, last activity, and a tail of each session's transcript. "
            "Use the tail to decide which session the user means, then address it by "
            "its `tty` in the other claude-session tools."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_claude_transcript",
        "description": (
            "Recent messages of one live Claude Code session (by `tty` from "
            "find_claude_sessions). Use it to answer whether the session finished, "
            "what it did, or whether it hit an error. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tty": {"type": "string", "description": "The session's tty."},
                "n": {
                    "type": "integer",
                    "description": "How many recent messages (default 20).",
                },
            },
            "required": ["tty"],
        },
    },
}

_SEND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_to_claude_session",
        "description": (
            "Type a message into a live Claude Code session's terminal tab (by `tty` "
            "from find_claude_sessions) and wait for its reply. The reply may take a "
            "while; on timeout you get the session's latest entries instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tty": {"type": "string", "description": "The session's tty."},
                "text": {"type": "string", "description": "The message to send."},
                "wait_seconds": {
                    "type": "integer",
                    "description": "How long to wait for the reply (default 120).",
                },
            },
            "required": ["tty", "text"],
        },
    },
}


def claude_session_tools(
    discovery: SessionFinder,
    driver: TerminalDriver,
    *,
    waiter: Callable[..., Optional[str]] = wait_for_reply,
    now: Callable[[], datetime] | None = None,
) -> list:
    clock = now or (lambda: datetime.now(timezone.utc))

    def _by_tty(tty: str) -> LiveSession | None:
        for session in discovery.list():
            if session.tty == tty:
                return session
        return None

    def find_claude_sessions() -> dict[str, Any]:
        sessions = discovery.list()
        result: dict[str, Any] = {"sessions": [s.to_dict() for s in sessions]}
        if not sessions:
            result["hint"] = (
                "No live Claude Code CLI session found in any terminal tab. "
                "IDE-embedded sessions have no terminal and cannot be reached."
            )
        return result

    def read_claude_transcript(tty: str, n: int = 20) -> dict[str, Any]:
        if not isinstance(tty, str) or not tty.strip():
            return {"error": "invalid_arguments"}
        session = _by_tty(tty)
        if session is None:
            return {"error": "session_gone"}
        if session.transcript is None:
            return {"error": "no_transcript"}
        # type() not isinstance(): booleans pass isinstance(x, int)
        count = min(n, _MAX_ENTRIES) if type(n) is int and n > 0 else 20
        return {
            "entries": [
                {
                    "role": e.role,
                    "text": e.text,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                }
                for e in transcript_tail(session.transcript, count)
            ]
        }

    def send_to_claude_session(
        tty: str, text: str, wait_seconds: int = 120
    ) -> dict[str, Any]:
        if (
            not isinstance(tty, str)
            or not tty.strip()
            or not isinstance(text, str)
            or not text.strip()
        ):
            return {"error": "invalid_arguments"}
        session = _by_tty(tty)
        if session is None:
            return {"error": "session_gone"}
        target = driver.find_target(session.tty)
        if target is None:
            return {"error": "session_gone"}
        after = clock()
        if not driver.send_text(target, text):
            return {"error": "send_failed"}
        if session.transcript is None:
            return {"status": "sent_no_reply", "last_entries": []}
        wait = (
            min(wait_seconds, _MAX_WAIT_SECONDS)
            if type(wait_seconds) is int and wait_seconds > 0
            else 120
        )
        reply = waiter(session.transcript, after, timeout=float(wait))
        if reply is not None:
            return {"status": "replied", "reply": reply}
        return {
            "status": "sent_no_reply",
            "last_entries": [e.text for e in transcript_tail(session.transcript, 5)],
        }

    find_claude_sessions.__coworker_schema__ = _FIND_SCHEMA
    read_claude_transcript.__coworker_schema__ = _READ_SCHEMA
    send_to_claude_session.__coworker_schema__ = _SEND_SCHEMA
    return [find_claude_sessions, read_claude_transcript, send_to_claude_session]


def claude_bridge_tools() -> list:
    """The production wiring: real discovery, real iTerm2 driver."""
    return claude_session_tools(SessionDiscovery(), ITerm2Driver())
