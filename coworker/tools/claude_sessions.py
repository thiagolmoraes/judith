"""Bridge tools — find live Claude Code CLI sessions, read their transcripts, send them
input. Thin adapter over coworker.claude_bridge (same factory-of-closures shape as
git_tools); all logic lives in the domain package, all deps are injected.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from ..claude_bridge.discovery import SessionDiscovery
from ..claude_bridge.models import LiveSession
from ..claude_bridge.registry import Watches, default_bridge_dir, read_sessions
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


_WATCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "watch_claude_session",
        "description": (
            "Watch a live Claude Code session (by `tty` from find_claude_sessions): "
            "when its current work finishes, a one-shot notification is sent to "
            "`notify_target`. Use the same 'platform:chat_id' target you would pass "
            "to send_message — normally the chat this conversation came from."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tty": {"type": "string", "description": "The session's tty."},
                "notify_target": {
                    "type": "string",
                    "description": "Where to notify, as 'platform:chat_id'.",
                },
            },
            "required": ["tty", "notify_target"],
        },
    },
}

_UNWATCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "unwatch_claude_session",
        "description": (
            "Cancel the pending finish-notification for a session (by `tty`)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tty": {"type": "string", "description": "The session's tty."}
            },
            "required": ["tty"],
        },
    },
}


def _prompt_token(session_id: str, message: str) -> str:
    """Binds a confirmation to the exact prompt text: if another permission request
    replaces the echoed one, the token no longer matches and the wrong command can't
    be approved. Stateless — recomputed on every call, nothing stored."""
    return hashlib.sha256(f"{session_id}\n{message}".encode()).hexdigest()[:12]


_RESPOND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "respond_to_claude_prompt",
        "description": (
            "Approve or deny the permission prompt a Claude Code session is waiting "
            "on (by `tty`). PROTOCOL: call once WITHOUT confirm_token — you get the "
            "exact prompt text and a token; show the prompt to the user verbatim and "
            "wait for their explicit confirmation; only then call again WITH the "
            "token. Never pass the token without the user's explicit confirmation, "
            "and never fabricate one. decision='approve' presses 1, 'deny' presses 3."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tty": {"type": "string", "description": "The session's tty."},
                "decision": {
                    "type": "string",
                    "enum": ["approve", "deny"],
                    "description": "What to do with the waiting prompt.",
                },
                "confirm_token": {
                    "type": "string",
                    "description": (
                        "Token from the confirmation_required response — only after "
                        "the user explicitly confirmed."
                    ),
                },
            },
            "required": ["tty", "decision"],
        },
    },
}


def claude_session_tools(
    discovery: SessionFinder,
    driver: TerminalDriver,
    *,
    waiter: Callable[..., Optional[str]] = wait_for_reply,
    now: Callable[[], datetime] | None = None,
    watches: Optional[Watches] = None,
    bridge_dir: Optional[Path] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list:
    wall_clock = now or (lambda: datetime.now(timezone.utc))

    def _by_tty(tty: str) -> LiveSession | None:
        for session in discovery.list():
            if session.tty == tty:
                return session
        return None

    def find_claude_sessions() -> dict[str, Any]:
        sessions = discovery.list()
        watched = set(watches.all()) if watches is not None else set()
        serialised = []
        for s in sessions:
            d = s.to_dict()
            if watches is not None:
                d["watched"] = s.session_id in watched
            serialised.append(d)
        result: dict[str, Any] = {"sessions": serialised}
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
        after = wall_clock()
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

    def watch_claude_session(tty: str, notify_target: str) -> dict[str, Any]:
        if (
            not isinstance(tty, str)
            or not tty.strip()
            or not isinstance(notify_target, str)
            or ":" not in notify_target
        ):
            return {"error": "invalid_arguments"}
        platform, _, chat_id = notify_target.partition(":")
        if not platform or not chat_id:
            return {"error": "invalid_arguments"}
        from ..connectors.senders import DEFAULT_SENDERS

        if platform not in DEFAULT_SENDERS:
            return {
                "error": "unknown_platform",
                "hint": f"known platforms: {', '.join(sorted(DEFAULT_SENDERS))}",
            }
        session = _by_tty(tty)
        if session is None:
            return {"error": "session_gone"}
        if session.session_id is None:
            return {
                "error": "no_registry",
                "hint": (
                    "This session has no hook registry entry — install the bridge "
                    "hooks with: python -m coworker.claude_bridge.install"
                ),
            }
        assert watches is not None
        if not watches.add(session.session_id, platform, chat_id):
            return {"error": "already_watched"}
        return {"status": "watching", "session_id": session.session_id}

    def unwatch_claude_session(tty: str) -> dict[str, Any]:
        if not isinstance(tty, str) or not tty.strip():
            return {"error": "invalid_arguments"}
        session = _by_tty(tty)
        if session is None:
            return {"error": "session_gone"}
        if session.session_id is None:
            return {"status": "not_watched"}
        assert watches is not None
        removed = watches.remove(session.session_id)
        return {"status": "unwatched" if removed else "not_watched"}

    find_claude_sessions.__coworker_schema__ = _FIND_SCHEMA
    read_claude_transcript.__coworker_schema__ = _READ_SCHEMA
    send_to_claude_session.__coworker_schema__ = _SEND_SCHEMA
    def respond_to_claude_prompt(
        tty: str, decision: str, confirm_token: Optional[str] = None
    ) -> dict[str, Any]:
        if (
            not isinstance(tty, str)
            or not tty.strip()
            or decision not in ("approve", "deny")
        ):
            return {"error": "invalid_arguments"}
        session = _by_tty(tty)
        if session is None:
            return {"error": "session_gone"}
        if session.session_id is None:
            return {
                "error": "no_registry",
                "hint": (
                    "This session has no hook registry entry — install the bridge "
                    "hooks with: python -m coworker.claude_bridge.install"
                ),
            }
        if session.status != "waiting_approval":
            return {"error": "not_waiting", "current_status": session.status}
        assert bridge_dir is not None
        states = {s.session_id: s for s in read_sessions(bridge_dir)}
        state = states.get(session.session_id)
        message = (state.message if state else None) or ""
        prompt = message or "a permission request"
        expected = _prompt_token(session.session_id, message)
        if confirm_token is None:
            return {
                "status": "confirmation_required",
                "prompt": prompt,
                "confirm_token": expected,
            }
        if confirm_token != expected:
            return {
                "error": "prompt_changed",
                "prompt": prompt,
                "confirm_token": expected,
            }
        target = driver.find_target(session.tty)
        if target is None:
            return {"error": "session_gone"}
        before = None
        if session.transcript is not None:
            try:
                before = session.transcript.stat().st_mtime
            except OSError:
                before = None
        key = "1" if decision == "approve" else "3"
        if not driver.send_keys(target, key):
            return {"error": "send_failed"}
        # Approval and denial both make the turn continue, so the transcript moving
        # is the observable effect of the keystroke landing — an atypical prompt
        # layout (e.g. a plan menu without option 3) shows up as sent_unverified.
        verified = False
        if before is not None:
            deadline = clock() + 10.0
            while clock() < deadline:
                sleep(1.0)
                try:
                    if session.transcript.stat().st_mtime > before:
                        verified = True
                        break
                except OSError:
                    break
        if not verified:
            return {"status": "sent_unverified", "decision": decision, "prompt": prompt}
        return {
            "status": "approved" if decision == "approve" else "denied",
            "verified": True,
            "prompt": prompt,
        }

    tools = [find_claude_sessions, read_claude_transcript, send_to_claude_session]
    if watches is not None:
        watch_claude_session.__coworker_schema__ = _WATCH_SCHEMA
        unwatch_claude_session.__coworker_schema__ = _UNWATCH_SCHEMA
        tools += [watch_claude_session, unwatch_claude_session]
    if bridge_dir is not None:
        respond_to_claude_prompt.__coworker_schema__ = _RESPOND_SCHEMA
        tools.append(respond_to_claude_prompt)
    return tools


def claude_bridge_tools() -> list:
    """The production wiring: real discovery, real iTerm2 driver, real watches."""
    bridge = default_bridge_dir()
    return claude_session_tools(
        SessionDiscovery(), ITerm2Driver(), watches=Watches(bridge), bridge_dir=bridge
    )
