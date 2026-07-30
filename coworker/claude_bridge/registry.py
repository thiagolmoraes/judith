"""The bridge registry — what the hook script wrote, read tolerantly.

`sessions/<id>.json` files are the source of truth for session status (spec decision:
files, not POSTs — durable when the app is closed). `watches.json` holds the one-shot
"tell me when it finishes" requests. Every read tolerates malformed or half-written
files (skip, never raise); every write is atomic (tmp + os.replace).
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Session ids come from hook payloads and become filenames — restrict to the uuid-ish
# shape Claude Code actually uses so a hostile id can't traverse out of sessions/.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def default_bridge_dir() -> Path:
    """The one place the bridge directory is defined (hook_script keeps its own literal
    copy by necessity — it must not import coworker)."""
    return Path.home() / ".claude" / "ow-bridge"


@dataclass
class SessionState:
    session_id: str
    transcript_path: Path | None
    cwd: str | None
    pid: int | None
    status: str
    message: str | None
    updated_at: datetime | None


def _parse_state(raw: object) -> SessionState | None:
    if not isinstance(raw, dict):
        return None
    session_id = raw.get("session_id")
    status = raw.get("status")
    if (
        not isinstance(session_id, str)
        or not _SESSION_ID_RE.match(session_id or "")
        or not isinstance(status, str)
    ):
        return None
    pid = raw.get("pid")
    transcript = raw.get("transcript_path")
    updated: datetime | None = None
    if isinstance(raw.get("updated_at"), str):
        try:
            updated = datetime.fromisoformat(raw["updated_at"])
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
        except ValueError:
            updated = None
    message = raw.get("message")
    return SessionState(
        session_id=session_id,
        transcript_path=Path(transcript) if isinstance(transcript, str) else None,
        cwd=raw.get("cwd") if isinstance(raw.get("cwd"), str) else None,
        pid=pid if isinstance(pid, int) and not isinstance(pid, bool) else None,
        status=status,
        message=message if isinstance(message, str) else None,
        updated_at=updated,
    )


def read_sessions(bridge_dir: Path) -> list[SessionState]:
    sessions = bridge_dir / "sessions"
    states: list[SessionState] = []
    try:
        files = sorted(sessions.glob("*.json"))
    except OSError:
        return []
    for file in files:
        try:
            raw = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        state = _parse_state(raw)
        if state is not None:
            states.append(state)
    return states


def prune(bridge_dir: Path, alive: Callable[[int], bool]) -> list[SessionState]:
    """Drop entries whose process is gone (crash — SessionEnd never fired) or that
    ended cleanly. Returns what was removed so the watcher can notify watched ones."""
    removed: list[SessionState] = []
    for state in read_sessions(bridge_dir):
        dead = state.pid is None or not alive(state.pid)
        if dead or state.status == "ended":
            removed.append(state)
            try:
                (bridge_dir / "sessions" / f"{state.session_id}.json").unlink()
            except OSError:
                pass
    return removed


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Watches:
    """One-shot notification requests, keyed by session_id. Stored in a single
    watches.json (tiny, owner-scale) — re-read on each call so concurrent instances
    see each other's consumption. A process-wide lock serialises the read-modify-write
    within this process (tools run in engine threads while the watcher polls); across
    processes the contract stays "last reader wins the pop" (spec)."""

    _lock = threading.Lock()

    def __init__(self, bridge_dir: Path) -> None:
        self._path = bridge_dir / "watches.json"

    def _load(self) -> dict[str, dict]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def all(self) -> dict[str, dict]:
        return self._load()

    def get(self, session_id: str) -> dict | None:
        return self._load().get(session_id)

    def add(
        self,
        session_id: str,
        platform: str,
        chat_id: str,
        *,
        now: Optional[Callable[[], datetime]] = None,
    ) -> bool:
        with self._lock:
            data = self._load()
            if session_id in data:
                return False
            clock = now or (lambda: datetime.now(timezone.utc))
            data[session_id] = {
                "platform": platform,
                "chat_id": chat_id,
                "created_at": clock().isoformat(),
            }
            _atomic_write_json(self._path, data)
            return True

    def remove(self, session_id: str) -> bool:
        with self._lock:
            data = self._load()
            if session_id not in data:
                return False
            del data[session_id]
            _atomic_write_json(self._path, data)
            return True

    def pop(self, session_id: str) -> dict | None:
        with self._lock:
            data = self._load()
            watch = data.pop(session_id, None)
            if watch is not None:
                _atomic_write_json(self._path, data)
            return watch
