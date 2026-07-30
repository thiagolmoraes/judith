"""Find live Claude Code CLI sessions: `ps` for processes, `lsof` for the cwd, then the
cwd munged into ~/.claude/projects/<dir>/ to locate the transcript.

Heuristics, documented: the transcript pick is "the newest .jsonl modified after the
process started" — exact when one file qualifies (confidence "matched"), a guess when
several or none do ("guessed"). Sessions with tty "??" (IDE/ACP-spawned) are skipped:
with no terminal there is nothing to type into. `ps -o lstart=` is locale-dependent, so
process age comes from `ps -o etime=` instead.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .models import LiveSession
from .registry import read_sessions
from .transcript import last_branch, tail

# Slack applied to "modified after process start": mtimes and etime are second-granular.
_START_SLACK = timedelta(seconds=60)
# Registry says idle but the transcript moved later than this → a new turn is running.
_RUNNING_SLACK = timedelta(seconds=5)
_TAIL_MESSAGES = 3
_TAIL_CHARS = 200


def munge_cwd(cwd: str) -> str:
    """Claude Code's project-dir name: every char outside [A-Za-z0-9] becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _parse_etime(raw: str) -> timedelta | None:
    """`[[dd-]hh:]mm:ss` → timedelta. "02:00" = 2 minutes; "1-03:00:00" = 27h."""
    raw = raw.strip()
    if not raw:
        return None
    days = 0
    if "-" in raw:
        day_part, raw = raw.split("-", 1)
        try:
            days = int(day_part)
        except ValueError:
            return None
    parts = raw.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        hours, (minutes, seconds) = 0, nums
    elif len(nums) == 3:
        hours, minutes, seconds = nums
    else:
        return None
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def _is_claude(command: str) -> bool:
    first = command.split()[0] if command.split() else ""
    return Path(first).name == "claude"


class SessionDiscovery:
    def __init__(
        self,
        *,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        projects_dir: Path | None = None,
        own_pid: int | None = None,
        now: Callable[[], datetime] | None = None,
        bridge_dir: Path | None = None,
    ) -> None:
        self._run = run
        self._projects = projects_dir or Path.home() / ".claude" / "projects"
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._bridge = bridge_dir or Path.home() / ".claude" / "ow-bridge"

    def list(self) -> list[LiveSession]:
        procs = self._processes()
        own = self._own_tree(procs)
        # Hook registry entries (phase 2), keyed by pid: an exact session_id and
        # transcript beat the mtime heuristic whenever a hook has fired.
        registry = {
            s.pid: s for s in read_sessions(self._bridge) if s.pid is not None
        }
        sessions: list[LiveSession] = []
        for pid, ppid, tty, command in procs:
            if not _is_claude(command) or tty in ("??", "-", "") or pid in own:
                continue
            cwd = self._cwd(pid)
            if not cwd:
                continue
            reg = registry.get(pid)
            if reg is not None and reg.transcript_path is not None:
                transcript, confidence = reg.transcript_path, "matched"
            else:
                transcript, confidence = self._pick_transcript(cwd, self._started(pid))
            branch = last_branch(transcript) if transcript else None
            entries = tail(transcript, _TAIL_MESSAGES) if transcript else []
            summary = "\n".join(f"{e.role}: {e.text[:_TAIL_CHARS]}" for e in entries)
            last_activity = None
            if transcript is not None:
                try:
                    last_activity = datetime.fromtimestamp(
                        transcript.stat().st_mtime, tz=timezone.utc
                    )
                except OSError:
                    pass
            status = None
            if reg is not None:
                status = reg.status
                if (
                    status == "idle"
                    and reg.updated_at is not None
                    and last_activity is not None
                    and last_activity - reg.updated_at > _RUNNING_SLACK
                ):
                    # The transcript moved after the last Stop: a new turn is running.
                    status = "running"
            sessions.append(
                LiveSession(
                    pid=pid,
                    tty=tty,
                    cwd=cwd,
                    branch=branch,
                    transcript=transcript,
                    transcript_confidence=confidence,
                    last_activity=last_activity,
                    tail=summary,
                    session_id=reg.session_id if reg is not None else None,
                    status=status,
                )
            )
        return sessions

    # -- process table ---------------------------------------------------------

    def _processes(self) -> list[tuple[int, int, str, str]]:
        out = self._exec(["ps", "-axo", "pid=,ppid=,tty=,command="])
        procs: list[tuple[int, int, str, str]] = []
        for line in (out or "").splitlines():
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            try:
                procs.append((int(parts[0]), int(parts[1]), parts[2], parts[3]))
            except ValueError:
                continue
        return procs

    def _own_tree(self, procs: list[tuple[int, int, str, str]]) -> set[int]:
        """Own pid + all descendants — the agent must never target the session that is
        answering it (a claude spawned via run_shell would be a child of this process)."""
        children: dict[int, list[int]] = {}
        for pid, ppid, _tty, _cmd in procs:
            children.setdefault(ppid, []).append(pid)
        tree = {self._own_pid}
        queue = [self._own_pid]
        while queue:
            for child in children.get(queue.pop(), []):
                if child not in tree:
                    tree.add(child)
                    queue.append(child)
        return tree

    def _cwd(self, pid: int) -> str | None:
        out = self._exec(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
        for line in (out or "").splitlines():
            if line.startswith("n"):
                return line[1:]
        return None

    def _started(self, pid: int) -> datetime | None:
        out = self._exec(["ps", "-o", "etime=", "-p", str(pid)])
        elapsed = _parse_etime(out or "")
        return self._now() - elapsed if elapsed else None

    def _pick_transcript(
        self, cwd: str, started: datetime | None
    ) -> tuple[Path | None, str]:
        project = self._projects / munge_cwd(cwd)
        try:
            candidates = sorted(
                project.glob("*.jsonl"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            candidates = []
        if not candidates:
            return None, "none"
        if started is not None:
            floor = (started - _START_SLACK).timestamp()
            recent = [f for f in candidates if f.stat().st_mtime >= floor]
            if len(recent) == 1:
                return recent[0], "matched"
            if recent:
                return recent[0], "guessed"
        return candidates[0], "guessed"

    def _exec(self, cmd: list[str]) -> str | None:
        try:
            out = self._run(cmd, capture_output=True, text=True, timeout=10)
        except Exception:
            return None
        return out.stdout if out.returncode == 0 else None
