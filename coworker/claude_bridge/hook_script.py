"""Claude Code hook → OpenWorker bridge registry.

Runs INSIDE Claude Code's hook mechanism (registered by
`python -m coworker.claude_bridge.install`), so: stdlib only, no imports from the rest
of coworker, and exit 0 no matter what — a hook must never break or slow the session it
observes. Reads the hook payload from stdin and writes one JSON state file per session
under <bridge_dir>/sessions/, atomically (tmp + os.replace).

The installer copies this file to ~/.claude/ow-bridge/hook.py and registers that copy,
so the hook keeps working even if the repo moves.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

_STATUS = {
    "Stop": "idle",
    "Notification": "waiting_approval",
    "SessionEnd": "ended",
}


def run(
    stdin_text: str,
    bridge_dir: Path,
    *,
    ppid: Optional[int] = None,
    now: Optional[Callable[[], datetime]] = None,
) -> int:
    """Process one hook payload. Always returns 0 — failure here must stay invisible
    to the Claude Code session that triggered the hook."""
    try:
        payload = json.loads(stdin_text)
        if not isinstance(payload, dict):
            return 0
        status = _STATUS.get(payload.get("hook_event_name") or "")
        session_id = payload.get("session_id")
        if status is None or not isinstance(session_id, str) or not session_id:
            return 0
        clock = now or (lambda: datetime.now(timezone.utc))
        state = {
            "session_id": session_id,
            "transcript_path": payload.get("transcript_path"),
            "cwd": payload.get("cwd"),
            "pid": os.getppid() if ppid is None else ppid,
            "status": status,
            "message": payload.get("message"),
            "updated_at": clock().isoformat(),
        }
        sessions = bridge_dir / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(sessions), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
            os.replace(tmp, sessions / f"{session_id}.json")
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except Exception:
        pass
    return 0


def main() -> int:
    bridge = Path(
        os.environ.get("COWORKER_BRIDGE_DIR", "")
        or Path.home() / ".claude" / "ow-bridge"
    )
    return run(sys.stdin.read(), bridge)


if __name__ == "__main__":
    sys.exit(main())
