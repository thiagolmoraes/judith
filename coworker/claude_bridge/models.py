"""LiveSession — one live Claude Code CLI session, as discovery sees it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class LiveSession:
    pid: int
    tty: str  # e.g. "ttys004" — the handle the tools pass around
    cwd: str
    branch: str | None
    transcript: Path | None
    transcript_confidence: str  # "matched" | "guessed" | "none"
    last_activity: datetime | None
    tail: str  # last few messages, summarised, for free-form matching
    session_id: str | None = None  # exact, from the hook registry when available
    status: str | None = None  # "idle" | "waiting_approval" | "running" | None

    def to_dict(self) -> dict:
        """Model-facing shape: the tty is the handle; filesystem paths (transcript, cwd)
        stay internal — the project basename plus branch and tail identify a session
        without leaking the local directory layout."""
        return {
            "pid": self.pid,
            "tty": self.tty,
            "project": Path(self.cwd).name,
            "branch": self.branch,
            "transcript_confidence": self.transcript_confidence,
            "last_activity": (
                self.last_activity.isoformat() if self.last_activity else None
            ),
            "tail": self.tail,
            "session_id": self.session_id,
            "status": self.status,
        }
