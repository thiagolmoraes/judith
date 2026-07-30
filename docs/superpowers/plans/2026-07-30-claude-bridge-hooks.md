# Claude Bridge Hooks (Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exact session status from Claude Code hooks (file registry) plus one-shot proactive "session finished" WhatsApp notifications for watched sessions.

**Architecture:** A standalone hook script writes per-session state files under `~/.claude/ow-bridge/`; a registry module reads/prunes them and manages one-shot watches; an asyncio watcher (started with the server's gateway, macOS only) detects status transitions on watched sessions and sends templated messages through the existing connector senders; phase-1 discovery consults the registry before its mtime heuristic. Spec: `docs/superpowers/specs/2026-07-30-claude-bridge-hooks-design.md`.

**Tech Stack:** Python ≥3.10 stdlib; existing `coworker.connectors.senders` for outbound; pytest.

## Global Constraints

- Same regime as phase 1: **no test touches real `ps`, `os.kill`, hooks, `~/.claude`, or the network** — inject runners/clocks/dirs (`tmp_path`), fake senders. Suite must pass on Linux CI.
- `hook_script.py` runs inside Claude Code's hook context: **stdlib only, no imports from other coworker modules, always exit 0** — a hook must never break the session it observes.
- Registry reads are tolerant: malformed/half-written JSON is skipped, never raised on (phase-1 transcript contract).
- All registry writes are atomic: write to a temp file in the same dir, then `os.replace`.
- Registration/wiring gated by `sys.platform == "darwin"` (watcher startup); modules stay importable everywhere.
- Hook events map: `Stop` → `idle`, `Notification` → `waiting_approval`, `SessionEnd` → `ended`; `SubagentStop` and unknown events write nothing.
- Watches are one-shot: consumed on `idle` notify and on ghost-prune notify; NOT consumed on `waiting_approval`.
- Commits: imperative sentence style, no `Co-Authored-By` trailer. Tests run with `source .venv/bin/activate`.
- Verified codebase facts: `Sender = Callable[[token, chat_id, text, thread_id], SendResult]` with `SendResult.ok: bool` (`coworker/connectors/senders.py`, `base.py:127`); token resolution is `_resolve_token(secrets, platform, chat_id)` (`coworker/connectors/tools.py:123`); the manager owns `self.secrets` (`coworker/server/manager.py:168`) and starts background loops in `start_gateway()` (`manager.py:2349`, `self.scheduler.start()` at 2353).
- Spec deviation (agreed at plan time): the installer ships as `python -m coworker.claude_bridge.install` instead of a `coworker claude-bridge` subcommand — the CLI's positional `skill` argument makes a subcommand awkward, and the installer copies the hook script to `~/.claude/ow-bridge/hook.py` so the registered command survives repo moves.

---

### Task 1: Hook script (`coworker/claude_bridge/hook_script.py`)

**Files:**
- Create: `coworker/claude_bridge/hook_script.py`
- Test: `tests/test_claude_bridge_hook_script.py`

**Interfaces:**
- Consumes: nothing (stdlib only, by constraint).
- Produces:
  - `run(stdin_text: str, bridge_dir: Path, *, ppid: int | None = None, now: Callable[[], datetime] | None = None) -> int` — always returns 0; testable core.
  - `main() -> int` — reads stdin + `COWORKER_BRIDGE_DIR` env (default `~/.claude/ow-bridge`), calls `run`.
  - State file format (consumed by Task 2): `sessions/<session_id>.json` with keys `session_id`, `transcript_path`, `cwd`, `pid`, `status`, `message`, `updated_at` (ISO, UTC).

- [ ] **Step 1: Write the failing tests**

`tests/test_claude_bridge_hook_script.py`:

```python
"""Hook script — stdin fixtures per event; must never fail, whatever the input."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from coworker.claude_bridge.hook_script import run

NOW = datetime(2026, 7, 30, 15, 0, 0, tzinfo=timezone.utc)


def _payload(event: str, **extra) -> str:
    data = {
        "hook_event_name": event,
        "session_id": "abc123",
        "transcript_path": "/Users/x/.claude/projects/-x-dev-webhook/abc123.jsonl",
        "cwd": "/Users/x/dev/webhook",
    }
    data.update(extra)
    return json.dumps(data)


def _state(bridge_dir: Path) -> dict:
    return json.loads(
        (bridge_dir / "sessions" / "abc123.json").read_text(encoding="utf-8")
    )


def test_stop_writes_idle_state(tmp_path: Path):
    assert run(_payload("Stop"), tmp_path, ppid=910, now=lambda: NOW) == 0
    state = _state(tmp_path)
    assert state == {
        "session_id": "abc123",
        "transcript_path": "/Users/x/.claude/projects/-x-dev-webhook/abc123.jsonl",
        "cwd": "/Users/x/dev/webhook",
        "pid": 910,
        "status": "idle",
        "message": None,
        "updated_at": "2026-07-30T15:00:00+00:00",
    }


def test_notification_records_waiting_with_message(tmp_path: Path):
    payload = _payload("Notification", message="Claude needs permission to run: git push")
    assert run(payload, tmp_path, ppid=910, now=lambda: NOW) == 0
    state = _state(tmp_path)
    assert state["status"] == "waiting_approval"
    assert state["message"] == "Claude needs permission to run: git push"


def test_session_end_records_ended(tmp_path: Path):
    assert run(_payload("SessionEnd"), tmp_path, ppid=910, now=lambda: NOW) == 0
    assert _state(tmp_path)["status"] == "ended"


def test_subagent_stop_and_unknown_write_nothing(tmp_path: Path):
    assert run(_payload("SubagentStop"), tmp_path, ppid=910, now=lambda: NOW) == 0
    assert run(_payload("SomethingNew"), tmp_path, ppid=910, now=lambda: NOW) == 0
    assert not (tmp_path / "sessions").exists()


def test_garbage_and_missing_fields_exit_zero(tmp_path: Path):
    assert run("not json {", tmp_path, ppid=910, now=lambda: NOW) == 0
    assert run(json.dumps({"hook_event_name": "Stop"}), tmp_path, ppid=910) == 0
    assert not (tmp_path / "sessions").exists()  # no session_id → nothing written


def test_unwritable_dir_exits_zero(tmp_path: Path):
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file, not a dir", encoding="utf-8")
    assert run(_payload("Stop"), blocked, ppid=910, now=lambda: NOW) == 0


def test_no_partial_file_on_write(tmp_path: Path):
    # Atomic write: after a successful run the only artifact is the final file.
    run(_payload("Stop"), tmp_path, ppid=910, now=lambda: NOW)
    names = [p.name for p in (tmp_path / "sessions").iterdir()]
    assert names == ["abc123.json"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_hook_script.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `hook_script.py`**

```python
"""Claude Code hook → Judith bridge registry.

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_hook_script.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add coworker/claude_bridge/hook_script.py tests/test_claude_bridge_hook_script.py
git commit -m "Record Claude Code session state from a Stop/Notification hook"
```

---

### Task 2: Registry (`coworker/claude_bridge/registry.py`)

**Files:**
- Create: `coworker/claude_bridge/registry.py`
- Test: `tests/test_claude_bridge_registry.py`

**Interfaces:**
- Consumes (Task 1): the `sessions/<id>.json` file format.
- Produces (used by Tasks 3–5):
  - `@dataclass SessionState: session_id: str; transcript_path: Path | None; cwd: str | None; pid: int | None; status: str; message: str | None; updated_at: datetime | None`
  - `read_sessions(bridge_dir: Path) -> list[SessionState]`
  - `prune(bridge_dir: Path, alive: Callable[[int], bool]) -> list[SessionState]` — removes dead/`ended` entries, returns them.
  - `class Watches` with `__init__(self, bridge_dir: Path)`, `add(session_id, platform, chat_id, *, now=None) -> bool` (False if already watched), `remove(session_id) -> bool`, `pop(session_id) -> dict | None`, `get(session_id) -> dict | None`, `all() -> dict[str, dict]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_claude_bridge_registry.py`:

```python
"""Bridge registry — tolerant reads, pid-liveness prune, one-shot watches."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from coworker.claude_bridge.registry import Watches, prune, read_sessions

NOW = datetime(2026, 7, 30, 15, 0, 0, tzinfo=timezone.utc)


def _write_state(bridge: Path, session_id: str, **overrides) -> Path:
    state = {
        "session_id": session_id,
        "transcript_path": f"/t/{session_id}.jsonl",
        "cwd": "/Users/x/dev/webhook",
        "pid": 910,
        "status": "idle",
        "message": None,
        "updated_at": "2026-07-30T15:00:00+00:00",
    }
    state.update(overrides)
    sessions = bridge / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    f = sessions / f"{session_id}.json"
    f.write_text(json.dumps(state), encoding="utf-8")
    return f


def test_read_sessions_parses_states(tmp_path: Path):
    _write_state(tmp_path, "aaa")
    _write_state(tmp_path, "bbb", status="waiting_approval", message="perm?")
    states = {s.session_id: s for s in read_sessions(tmp_path)}
    assert set(states) == {"aaa", "bbb"}
    assert states["aaa"].status == "idle"
    assert states["aaa"].pid == 910
    assert states["aaa"].transcript_path == Path("/t/aaa.jsonl")
    assert states["aaa"].updated_at == NOW
    assert states["bbb"].message == "perm?"


def test_read_sessions_skips_garbage_and_missing_dir(tmp_path: Path):
    assert read_sessions(tmp_path) == []  # no sessions/ dir at all
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "bad.json").write_text("{ half writ", encoding="utf-8")
    (sessions / "noid.json").write_text(json.dumps({"status": "idle"}), encoding="utf-8")
    _write_state(tmp_path, "ok")
    assert [s.session_id for s in read_sessions(tmp_path)] == ["ok"]


def test_read_sessions_tolerates_bad_fields(tmp_path: Path):
    _write_state(tmp_path, "odd", pid="not-an-int", updated_at="not-a-date",
                 transcript_path=None)
    (state,) = read_sessions(tmp_path)
    assert state.pid is None
    assert state.updated_at is None
    assert state.transcript_path is None


def test_prune_removes_dead_and_ended(tmp_path: Path):
    _write_state(tmp_path, "alive", pid=1)
    _write_state(tmp_path, "dead", pid=2)
    _write_state(tmp_path, "done", pid=1, status="ended")
    removed = prune(tmp_path, alive=lambda pid: pid == 1)
    assert sorted(s.session_id for s in removed) == ["dead", "done"]
    assert [s.session_id for s in read_sessions(tmp_path)] == ["alive"]


def test_prune_entry_without_pid_is_dead(tmp_path: Path):
    _write_state(tmp_path, "nopid", pid=None)
    removed = prune(tmp_path, alive=lambda pid: True)
    assert [s.session_id for s in removed] == ["nopid"]


def test_watches_one_shot_pop(tmp_path: Path):
    w = Watches(tmp_path)
    assert w.add("aaa", "whatsapp_evolution", "5511@s.whatsapp.net", now=lambda: NOW)
    assert not w.add("aaa", "whatsapp_evolution", "5511@s.whatsapp.net")  # already
    got = w.pop("aaa")
    assert got == {
        "platform": "whatsapp_evolution",
        "chat_id": "5511@s.whatsapp.net",
        "created_at": "2026-07-30T15:00:00+00:00",
    }
    assert w.pop("aaa") is None  # consumed
    # persisted: a fresh instance sees the same (now empty) state
    assert Watches(tmp_path).all() == {}


def test_watches_get_does_not_consume_and_remove_is_idempotent(tmp_path: Path):
    w = Watches(tmp_path)
    w.add("aaa", "whatsapp_evolution", "5511@x", now=lambda: NOW)
    assert w.get("aaa") is not None
    assert w.get("aaa") is not None
    assert w.remove("aaa") is True
    assert w.remove("aaa") is False


def test_watches_tolerates_corrupt_file(tmp_path: Path):
    (tmp_path / "watches.json").write_text("{ nope", encoding="utf-8")
    w = Watches(tmp_path)
    assert w.all() == {}
    assert w.add("aaa", "whatsapp_evolution", "5511@x", now=lambda: NOW)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `registry.py`**

```python
"""The bridge registry — what the hook script wrote, read tolerantly.

`sessions/<id>.json` files are the source of truth for session status (spec decision:
files, not POSTs — durable when the app is closed). `watches.json` holds the one-shot
"tell me when it finishes" requests. Every read tolerates malformed or half-written
files (skip, never raise); every write is atomic (tmp + os.replace).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional


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
    if not isinstance(session_id, str) or not session_id or not isinstance(status, str):
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
    see each other's consumption."""

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
        data = self._load()
        if session_id not in data:
            return False
        del data[session_id]
        _atomic_write_json(self._path, data)
        return True

    def pop(self, session_id: str) -> dict | None:
        data = self._load()
        watch = data.pop(session_id, None)
        if watch is not None:
            _atomic_write_json(self._path, data)
        return watch
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_registry.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add coworker/claude_bridge/registry.py tests/test_claude_bridge_registry.py
git commit -m "Read and prune the bridge registry, with one-shot watches"
```

---

### Task 3: Watcher (`coworker/claude_bridge/watcher.py`)

**Files:**
- Create: `coworker/claude_bridge/watcher.py`
- Test: `tests/test_claude_bridge_watcher.py`

**Interfaces:**
- Consumes (Task 2): `read_sessions`, `prune`, `Watches`, `SessionState`. (Phase 1): `transcript.tail`.
- Produces (used by Task 6 wiring):
  - `class Notifier(Protocol)` with `send(self, platform: str, chat_id: str, text: str) -> bool`
  - `class BridgeWatcher` with `__init__(self, bridge_dir: Path, notifier: Notifier, *, alive: Callable[[int], bool] | None = None, poll_seconds: float = 2.0)`, `poll_once() -> None` (sync, fully testable), `start() -> None` / `stop() -> None` (asyncio task around `poll_once`, mirroring `coworker/automation/scheduler.py`).
  - `class ConnectorNotifier` with `__init__(self, secrets, senders: dict | None = None)` implementing `Notifier` via `coworker.connectors` senders.

- [ ] **Step 1: Write the failing tests**

`tests/test_claude_bridge_watcher.py`:

```python
"""Watcher — fake registry dir, fake notifier, injected liveness. No asyncio needed:
poll_once() is the whole behaviour; start()/stop() are a thin loop around it."""

from __future__ import annotations

import json
from pathlib import Path

from coworker.claude_bridge.registry import Watches
from coworker.claude_bridge.watcher import BridgeWatcher, ConnectorNotifier


def _write_state(bridge: Path, session_id: str, **overrides) -> None:
    state = {
        "session_id": session_id,
        "transcript_path": None,
        "cwd": "/Users/x/dev/webhook",
        "pid": 910,
        "status": "idle",
        "message": None,
        "updated_at": "2026-07-30T15:00:00+00:00",
    }
    state.update(overrides)
    sessions = bridge / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.json").write_text(json.dumps(state), encoding="utf-8")


def _transcript(tmp_path: Path, text: str) -> str:
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-07-30T15:00:00.000Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            }
        ),
        encoding="utf-8",
    )
    return str(path)


class FakeNotifier:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.sent: list[tuple[str, str, str]] = []

    def send(self, platform: str, chat_id: str, text: str) -> bool:
        self.sent.append((platform, chat_id, text))
        return self.ok


def _watch(bridge: Path, session_id: str = "aaa") -> None:
    Watches(bridge).add(session_id, "whatsapp_evolution", "5511@s.whatsapp.net")


def test_idle_watched_session_notifies_once_and_consumes(tmp_path: Path):
    # Session is running when the watch is placed, then goes idle.
    _write_state(tmp_path, "aaa", status="waiting_approval")
    _watch(tmp_path)
    notifier = FakeNotifier()
    watcher = BridgeWatcher(tmp_path, notifier, alive=lambda pid: True)
    watcher.poll_once()  # baseline: waiting_approval → notifies the waiting state
    _write_state(
        tmp_path, "aaa", status="idle",
        transcript_path=_transcript(tmp_path, "refactor done, 12 files"),
    )
    watcher.poll_once()
    finished = [s for s in notifier.sent if "finished" in s[2]]
    assert len(finished) == 1
    platform, chat_id, text = finished[0]
    assert (platform, chat_id) == ("whatsapp_evolution", "5511@s.whatsapp.net")
    assert "webhook" in text  # project name
    assert "refactor done, 12 files" in text  # transcript snippet
    watcher.poll_once()
    assert len([s for s in notifier.sent if "finished" in s[2]]) == 1  # consumed
    assert Watches(tmp_path).get("aaa") is None


def test_session_already_idle_when_watched_notifies_on_first_poll(tmp_path: Path):
    # Watch placed after the turn ended (registry already says idle): first poll fires.
    _write_state(tmp_path, "aaa", status="idle")
    _watch(tmp_path)
    notifier = FakeNotifier()
    BridgeWatcher(tmp_path, notifier, alive=lambda pid: True).poll_once()
    assert len(notifier.sent) == 1


def test_waiting_approval_notifies_without_consuming(tmp_path: Path):
    _write_state(tmp_path, "aaa", status="waiting_approval",
                 message="permission to run: git push")
    _watch(tmp_path)
    notifier = FakeNotifier()
    watcher = BridgeWatcher(tmp_path, notifier, alive=lambda pid: True)
    watcher.poll_once()
    assert len(notifier.sent) == 1
    assert "git push" in notifier.sent[0][2]
    assert Watches(tmp_path).get("aaa") is not None  # still watched
    watcher.poll_once()
    assert len(notifier.sent) == 1  # same state → no repeat


def test_ghost_watched_session_notifies_closed_and_consumes(tmp_path: Path):
    _write_state(tmp_path, "aaa", status="idle", pid=999)
    _watch(tmp_path)
    notifier = FakeNotifier()
    BridgeWatcher(tmp_path, notifier, alive=lambda pid: False).poll_once()
    assert len(notifier.sent) == 1
    assert "closed" in notifier.sent[0][2]
    assert Watches(tmp_path).get("aaa") is None
    assert not list((tmp_path / "sessions").glob("*.json"))  # pruned


def test_unwatched_sessions_never_notify(tmp_path: Path):
    _write_state(tmp_path, "aaa", status="idle")
    _write_state(tmp_path, "bbb", status="waiting_approval")
    notifier = FakeNotifier()
    BridgeWatcher(tmp_path, notifier, alive=lambda pid: True).poll_once()
    assert notifier.sent == []


def test_notifier_failure_does_not_crash_and_watch_survives(tmp_path: Path):
    _write_state(tmp_path, "aaa", status="idle")
    _watch(tmp_path)
    notifier = FakeNotifier(ok=False)
    watcher = BridgeWatcher(tmp_path, notifier, alive=lambda pid: True)
    watcher.poll_once()  # send fails → watch NOT consumed, retried next poll
    assert Watches(tmp_path).get("aaa") is not None
    notifier.ok = True
    watcher.poll_once()
    assert Watches(tmp_path).get("aaa") is None


def test_empty_registry_is_a_noop(tmp_path: Path):
    notifier = FakeNotifier()
    BridgeWatcher(tmp_path, notifier, alive=lambda pid: True).poll_once()
    assert notifier.sent == []


class _FakeSecrets:
    def __init__(self, creds: dict):
        self._creds = creds

    def get(self, key: str):
        return self._creds.get(key)


def test_connector_notifier_sends_via_sender_registry():
    calls: list[tuple] = []

    def fake_sender(token, chat_id, text, thread_id=None):
        calls.append((token, chat_id, text, thread_id))

        class R:
            ok = True

        return R()

    secrets = _FakeSecrets(
        {"whatsapp_evolution:default": {"base_url": "http://e", "api_key": "k",
                                        "instance": "openworker"}}
    )
    notifier = ConnectorNotifier(secrets, senders={"whatsapp_evolution": fake_sender})
    assert notifier.send("whatsapp_evolution", "5511@s.whatsapp.net", "hi") is True
    assert calls == [("http://e|k|openworker", "5511@s.whatsapp.net", "hi", None)]


def test_connector_notifier_false_on_missing_token_or_platform():
    notifier = ConnectorNotifier(_FakeSecrets({}), senders={})
    assert notifier.send("whatsapp_evolution", "5511@x", "hi") is False


def test_connector_notifier_false_on_sender_crash():
    def broken(token, chat_id, text, thread_id=None):
        raise RuntimeError("boom")

    secrets = _FakeSecrets({"whatsapp_evolution:default": {"base_url": "http://e"}})
    notifier = ConnectorNotifier(secrets, senders={"whatsapp_evolution": broken})
    assert notifier.send("whatsapp_evolution", "5511@x", "hi") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_watcher.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `watcher.py`**

```python
"""The bridge watcher — turns registry transitions into WhatsApp notifications.

Runs as an asyncio task in the always-on server (started with the gateway, macOS
only), polling the registry every couple of seconds. Only *watched* sessions ever
notify (spec: opt-in, one-shot — notify-every-turn would spam a chatty terminal).
The notification is a template plus a transcript snippet: no model turn, no cost.

Send failures leave the watch in place — the next poll retries. A session that died
without finishing (pid gone, SessionEnd never fired) notifies "closed before
finishing" and consumes the watch.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Callable, Optional, Protocol

from .registry import SessionState, Watches, prune, read_sessions
from .transcript import tail

logger = logging.getLogger("coworker.claude_bridge")

_SNIPPET_CHARS = 300


class Notifier(Protocol):
    def send(self, platform: str, chat_id: str, text: str) -> bool: ...


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _project(state: SessionState) -> str:
    return Path(state.cwd).name if state.cwd else state.session_id[:8]


def _snippet(state: SessionState) -> str:
    if state.transcript_path is None:
        return ""
    entries = [e for e in tail(state.transcript_path, 5) if e.role == "assistant"]
    if not entries:
        return ""
    return entries[-1].text[:_SNIPPET_CHARS]


class BridgeWatcher:
    def __init__(
        self,
        bridge_dir: Path,
        notifier: Notifier,
        *,
        alive: Optional[Callable[[int], bool]] = None,
        poll_seconds: float = 2.0,
    ) -> None:
        self._dir = bridge_dir
        self._notifier = notifier
        self._alive = alive or _pid_alive
        self._poll_seconds = poll_seconds
        self._watches = Watches(bridge_dir)
        # session_id → last status we notified for (dedup within this process).
        self._notified: dict[str, str] = {}
        self._task: Optional[asyncio.Task] = None

    # -- one poll: the entire behaviour, synchronous and testable ------------------

    def poll_once(self) -> None:
        try:
            self._poll()
        except Exception:  # never let a bad poll kill the loop
            logger.exception("bridge watcher poll failed")

    def _poll(self) -> None:
        for state in prune(self._dir, self._alive):
            watch = self._watches.get(state.session_id)
            if watch is None:
                continue
            text = f"⚠️ Claude session '{_project(state)}' closed before finishing."
            if self._notifier.send(watch["platform"], watch["chat_id"], text):
                self._watches.pop(state.session_id)
            self._notified.pop(state.session_id, None)

        for state in read_sessions(self._dir):
            watch = self._watches.get(state.session_id)
            if watch is None:
                self._notified.pop(state.session_id, None)
                continue
            if self._notified.get(state.session_id) == state.status:
                continue
            if state.status == "idle":
                snippet = _snippet(state)
                text = f"✅ Claude session '{_project(state)}' finished."
                if snippet:
                    text = f"{text}\n\n{snippet}"
                if self._notifier.send(watch["platform"], watch["chat_id"], text):
                    self._watches.pop(state.session_id)
                    self._notified[state.session_id] = state.status
            elif state.status == "waiting_approval":
                detail = state.message or "a permission request"
                text = (
                    f"⏸ Claude session '{_project(state)}' is waiting for approval: "
                    f"{detail}"
                )
                if self._notifier.send(watch["platform"], watch["chat_id"], text):
                    # Watch NOT consumed — the session hasn't finished.
                    self._notified[state.session_id] = state.status

    # -- lifecycle (mirrors automation.Scheduler) -----------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_event_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            self.poll_once()
            await asyncio.sleep(self._poll_seconds)


class ConnectorNotifier:
    """Production Notifier: the same stateless senders `send_message` uses."""

    def __init__(self, secrets, senders: Optional[dict] = None) -> None:
        from ..connectors.senders import DEFAULT_SENDERS

        self._secrets = secrets
        self._senders = DEFAULT_SENDERS if senders is None else senders

    def send(self, platform: str, chat_id: str, text: str) -> bool:
        from ..connectors.tools import _resolve_token

        sender = self._senders.get(platform)
        if sender is None:
            return False
        token = _resolve_token(self._secrets, platform, chat_id)
        if not token:
            return False
        try:
            return bool(sender(token, chat_id, text, None).ok)
        except Exception:
            logger.exception("bridge notification send failed (%s)", platform)
            return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_watcher.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add coworker/claude_bridge/watcher.py tests/test_claude_bridge_watcher.py
git commit -m "Watch the bridge registry and notify watched sessions"
```

---

### Task 4: Discovery upgrade — registry-first matching

**Files:**
- Modify: `coworker/claude_bridge/discovery.py` (constructor + `list()` + `_pick_transcript` call site)
- Modify: `coworker/claude_bridge/models.py` (new fields)
- Test: `tests/test_claude_bridge_discovery.py` (extend)

**Interfaces:**
- Consumes (Task 2): `read_sessions(bridge_dir) -> list[SessionState]`.
- Produces (used by Task 5):
  - `LiveSession` gains `session_id: str | None = None` and `status: str | None = None`; `to_dict()` includes both.
  - `SessionDiscovery.__init__` gains keyword `bridge_dir: Path | None = None` (default `Path.home() / ".claude" / "ow-bridge"`).
  - Matching rule: a registry entry with the same pid supplies exact `session_id` + `transcript_path` (`transcript_confidence: "matched"`) and `status`; `status` becomes `"running"` when the transcript's mtime is newer than the registry's `updated_at` by >5 s (a new turn started since the last Stop). No registry match → phase-1 heuristic unchanged, `status=None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_bridge_discovery.py`:

```python
def _write_registry(bridge: Path, session_id: str, pid: int, transcript: Path,
                    cwd: str, status: str = "idle",
                    updated_at: str = "2026-07-30T11:59:30+00:00") -> None:
    sessions = bridge / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "transcript_path": str(transcript),
                "cwd": cwd,
                "pid": pid,
                "status": status,
                "message": None,
                "updated_at": updated_at,
            }
        ),
        encoding="utf-8",
    )


def test_registry_match_beats_mtime_heuristic(tmp_path: Path):
    projects = tmp_path / "projects"
    bridge = tmp_path / "bridge"
    cwd = "/Users/x/dev/webhook"
    # Two candidates: the mtime heuristic alone would guess the newer one...
    right = _make_project(projects, cwd, "right.jsonl", "the real one", NOW.timestamp() - 30)
    _make_project(projects, cwd, "decoy.jsonl", "newer decoy", NOW.timestamp() - 5)
    # ...but the registry says pid 910 is session "right".
    _write_registry(bridge, "right", 910, right, cwd)
    runner = FakeRunner(PS, cwds={910: cwd}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner, projects_dir=projects, own_pid=99999, now=lambda: NOW,
        bridge_dir=bridge,
    )
    (s,) = disc.list()
    assert s.session_id == "right"
    assert s.transcript is not None and s.transcript.name == "right.jsonl"
    assert s.transcript_confidence == "matched"
    assert s.status == "idle"
    d = s.to_dict()
    assert d["session_id"] == "right"
    assert d["status"] == "idle"


def test_registry_idle_but_newer_transcript_means_running(tmp_path: Path):
    import os as _os

    projects = tmp_path / "projects"
    bridge = tmp_path / "bridge"
    cwd = "/Users/x/dev/webhook"
    t = _make_project(projects, cwd, "aaa.jsonl", "working...", NOW.timestamp() - 60)
    # registry Stop happened at 11:58; transcript moved at 11:59 → a new turn started
    _write_registry(bridge, "aaa", 910, t, cwd, status="idle",
                    updated_at="2026-07-30T11:58:00+00:00")
    _os.utime(t, (NOW.timestamp() - 60, NOW.timestamp() - 60))
    runner = FakeRunner(PS, cwds={910: cwd}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner, projects_dir=projects, own_pid=99999, now=lambda: NOW,
        bridge_dir=bridge,
    )
    (s,) = disc.list()
    assert s.status == "running"


def test_no_registry_entry_falls_back_to_heuristic(tmp_path: Path):
    projects = tmp_path / "projects"
    cwd = "/Users/x/dev/webhook"
    _make_project(projects, cwd, "aaa.jsonl", "hi", NOW.timestamp() - 60)
    runner = FakeRunner(PS, cwds={910: cwd}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner, projects_dir=projects, own_pid=99999, now=lambda: NOW,
        bridge_dir=tmp_path / "empty-bridge",
    )
    (s,) = disc.list()
    assert s.session_id is None
    assert s.status is None
    assert s.transcript is not None  # heuristic still worked
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_discovery.py -v`
Expected: new tests FAIL (`bridge_dir` unknown kwarg)

- [ ] **Step 3: Implement**

In `models.py`, add to `LiveSession` (after `tail: str`):

```python
    session_id: str | None = None  # exact, from the hook registry when available
    status: str | None = None  # "idle" | "waiting_approval" | "running" | None
```

and in `to_dict()`, add to the returned dict:

```python
            "session_id": self.session_id,
            "status": self.status,
```

In `discovery.py`:

- Add import: `from .registry import SessionState, read_sessions`.
- Add module constant: `_RUNNING_SLACK = timedelta(seconds=5)`.
- `__init__` gains `bridge_dir: Path | None = None`; store
  `self._bridge = bridge_dir or Path.home() / ".claude" / "ow-bridge"`.
- In `list()`, before the loop: `registry = {s.pid: s for s in read_sessions(self._bridge) if s.pid is not None}`.
- Inside the loop, replace the transcript/branch/entries block head with:

```python
            reg = registry.get(pid)
            if reg is not None and reg.transcript_path is not None:
                transcript, confidence = reg.transcript_path, "matched"
            else:
                transcript, confidence = self._pick_transcript(cwd, self._started(pid))
```

- After `last_activity` is computed, derive status (only when `reg` exists):

```python
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
```

- Pass `session_id=reg.session_id if reg is not None else None` and `status=status`
  to the `LiveSession(...)` constructor call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_discovery.py tests/test_claude_bridge_transcript.py -v`
Expected: all PASS (old tests untouched: they pass no `bridge_dir`, defaulting to a dir that exists but has no `sessions/` for them — pass an explicit empty `tmp_path` bridge only in the new tests)

Note: the old discovery tests construct `SessionDiscovery` without `bridge_dir`, which
defaults to the real `~/.claude/ow-bridge`. To keep them hermetic, update the three
existing constructor call sites in the old tests to pass `bridge_dir=tmp_path / "nobridge"`.

- [ ] **Step 5: Commit**

```bash
git add coworker/claude_bridge/models.py coworker/claude_bridge/discovery.py tests/test_claude_bridge_discovery.py
git commit -m "Let the hook registry give discovery exact sessions and status"
```

---

### Task 5: Tool upgrades — watch/unwatch + status surfacing

**Files:**
- Modify: `coworker/tools/claude_sessions.py`
- Test: `tests/test_claude_session_tools.py` (extend)

**Interfaces:**
- Consumes (Tasks 2, 4): `Watches`, `LiveSession.session_id`/`status`.
- Produces:
  - `claude_session_tools(discovery, driver, *, waiter=..., now=..., watches: Watches | None = None)` — two new closures `watch_claude_session(tty, notify_target)` and `unwatch_claude_session(tty)`, returned only when `watches is not None`.
  - `notify_target` format: `"platform:chat_id"` — the same target string the agent already uses with `send_message` (split on the first `:`).
  - `find_claude_sessions` entries gain `watched: bool` (when `watches` is wired).
  - `claude_bridge_tools()` wires `Watches(Path.home() / ".claude" / "ow-bridge")`.
  - Errors: `session_gone` (tty unknown), `no_registry` (session has no `session_id` — hooks not installed; error text mentions `python -m coworker.claude_bridge.install`), `already_watched`, `invalid_arguments`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_claude_session_tools.py`:

```python
def _session_with_id(tty: str = "ttys000", session_id: str | None = "sess-1"):
    s = _session(tty=tty)
    s.session_id = session_id
    s.status = "idle" if session_id else None
    return s


def _watched_tools(tmp_path, sessions):
    from coworker.claude_bridge.registry import Watches

    watches = Watches(tmp_path)
    tools = claude_session_tools(
        FakeDiscovery(sessions), FakeDriver(), watches=watches
    )
    return {t.__name__: t for t in tools}, watches


def test_watch_tools_absent_without_watches():
    names = {t.__name__ for t in claude_session_tools(FakeDiscovery([]), FakeDriver())}
    assert "watch_claude_session" not in names


def test_watch_records_notify_target(tmp_path):
    t, watches = _watched_tools(tmp_path, [_session_with_id()])
    result = t["watch_claude_session"](
        tty="ttys000", notify_target="whatsapp_evolution:5511@s.whatsapp.net"
    )
    assert result == {"status": "watching", "session_id": "sess-1"}
    watch = watches.get("sess-1")
    assert watch["platform"] == "whatsapp_evolution"
    assert watch["chat_id"] == "5511@s.whatsapp.net"


def test_watch_errors(tmp_path):
    t, _ = _watched_tools(tmp_path, [_session_with_id()])
    assert t["watch_claude_session"](tty="ttys9", notify_target="whatsapp_evolution:x") == {
        "error": "session_gone"
    }
    assert t["watch_claude_session"](tty="ttys000", notify_target="no-colon") == {
        "error": "invalid_arguments"
    }
    assert t["watch_claude_session"](tty="ttys000", notify_target="") == {
        "error": "invalid_arguments"
    }
    t["watch_claude_session"](tty="ttys000", notify_target="whatsapp_evolution:x")
    assert t["watch_claude_session"](tty="ttys000", notify_target="whatsapp_evolution:x") == {
        "error": "already_watched"
    }


def test_watch_without_registry_session_id(tmp_path):
    t, _ = _watched_tools(tmp_path, [_session_with_id(session_id=None)])
    result = t["watch_claude_session"](
        tty="ttys000", notify_target="whatsapp_evolution:x"
    )
    assert result["error"] == "no_registry"
    assert "coworker.claude_bridge.install" in result["hint"]


def test_unwatch_is_idempotent(tmp_path):
    t, _ = _watched_tools(tmp_path, [_session_with_id()])
    t["watch_claude_session"](tty="ttys000", notify_target="whatsapp_evolution:x")
    assert t["unwatch_claude_session"](tty="ttys000") == {"status": "unwatched"}
    assert t["unwatch_claude_session"](tty="ttys000") == {"status": "not_watched"}


def test_find_reports_watched_and_status(tmp_path):
    t, watches = _watched_tools(tmp_path, [_session_with_id()])
    watches.add("sess-1", "whatsapp_evolution", "x")
    (entry,) = t["find_claude_sessions"]()["sessions"]
    assert entry["watched"] is True
    assert entry["status"] == "idle"
    assert entry["session_id"] == "sess-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_claude_session_tools.py -v`
Expected: new tests FAIL (`watches` unknown kwarg / KeyError)

- [ ] **Step 3: Implement**

In `coworker/tools/claude_sessions.py`:

- Imports: add `from pathlib import Path` and `from ..claude_bridge.registry import Watches`.
- Two new schemas:

```python
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
```

- `claude_session_tools` signature gains `watches: Optional[Watches] = None`.
- Inside the factory, after `send_to_claude_session`:

```python
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
```

- In `find_claude_sessions`, when `watches is not None`, decorate each serialised
  session:

```python
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
```

- Attach schemas and extend the returned list only when watching is wired:

```python
    tools = [find_claude_sessions, read_claude_transcript, send_to_claude_session]
    if watches is not None:
        watch_claude_session.__coworker_schema__ = _WATCH_SCHEMA
        unwatch_claude_session.__coworker_schema__ = _UNWATCH_SCHEMA
        tools += [watch_claude_session, unwatch_claude_session]
    return tools
```

- `claude_bridge_tools()` becomes:

```python
def claude_bridge_tools() -> list:
    """The production wiring: real discovery, real iTerm2 driver, real watches."""
    bridge = Path.home() / ".claude" / "ow-bridge"
    return claude_session_tools(
        SessionDiscovery(), ITerm2Driver(), watches=Watches(bridge)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_claude_session_tools.py -v`
Expected: all PASS (including the phase-1 registration test — five tools now on darwin, still none on linux)

- [ ] **Step 5: Commit**

```bash
git add coworker/tools/claude_sessions.py tests/test_claude_session_tools.py
git commit -m "Add watch and unwatch tools over the bridge registry"
```

---

### Task 6: Installer (`coworker/claude_bridge/install.py`)

**Files:**
- Create: `coworker/claude_bridge/install.py`
- Test: `tests/test_claude_bridge_installer.py`

**Interfaces:**
- Consumes (Task 1): the `hook_script.py` source file (copied verbatim).
- Produces:
  - `install(settings_path: Path, bridge_dir: Path, *, python: str | None = None) -> list[str]` — returns the hook events registered/updated.
  - `uninstall(settings_path: Path, bridge_dir: Path) -> list[str]` — removes only ours.
  - `main(argv) -> int` — `python -m coworker.claude_bridge.install [--uninstall]` on the real paths.
  - Marker: our hook commands contain `ow-bridge/hook.py` — that substring identifies our entries for idempotence and uninstall.

- [ ] **Step 1: Write the failing tests**

`tests/test_claude_bridge_installer.py`:

```python
"""Installer — merge into settings.json, never clobber; idempotent; clean uninstall."""

from __future__ import annotations

import json
from pathlib import Path

from coworker.claude_bridge.install import install, uninstall

EVENTS = ["Stop", "Notification", "SessionEnd"]


def _settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fresh_install_registers_three_hooks(tmp_path: Path):
    settings = tmp_path / "settings.json"
    bridge = tmp_path / "ow-bridge"
    events = install(settings, bridge, python="/usr/bin/python3")
    assert sorted(events) == sorted(EVENTS)
    data = _settings(settings)
    for event in EVENTS:
        (entry,) = data["hooks"][event]
        (hook,) = entry["hooks"]
        assert hook["type"] == "command"
        assert '/usr/bin/python3' in hook["command"]
        assert str(bridge / "hook.py") in hook["command"]
    # the hook script was copied and is real python
    copied = (bridge / "hook.py").read_text(encoding="utf-8")
    assert "def run(" in copied


def test_install_merges_with_existing_user_hooks(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "say done"}]}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    install(settings, tmp_path / "ow-bridge", python="py")
    data = _settings(settings)
    assert data["model"] == "opus"  # untouched
    stop_cmds = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
    assert "say done" in stop_cmds  # user hook preserved
    assert any("ow-bridge/hook.py" in c for c in stop_cmds)  # ours added


def test_install_is_idempotent(tmp_path: Path):
    settings = tmp_path / "settings.json"
    bridge = tmp_path / "ow-bridge"
    install(settings, bridge, python="py")
    once = _settings(settings)
    install(settings, bridge, python="py")
    assert _settings(settings) == once


def test_install_creates_backup_once(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    install(settings, tmp_path / "ow-bridge", python="py")
    backup = tmp_path / "settings.json.ow-backup"
    assert json.loads(backup.read_text(encoding="utf-8")) == {"model": "opus"}
    # a second install must not overwrite the original backup
    install(settings, tmp_path / "ow-bridge", python="py")
    assert json.loads(backup.read_text(encoding="utf-8")) == {"model": "opus"}


def test_uninstall_removes_only_ours(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]
                }
            }
        ),
        encoding="utf-8",
    )
    bridge = tmp_path / "ow-bridge"
    install(settings, bridge, python="py")
    removed = uninstall(settings, bridge)
    assert sorted(removed) == sorted(EVENTS)
    data = _settings(settings)
    stop_cmds = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
    assert stop_cmds == ["say done"]  # user hook survives
    for event in ("Notification", "SessionEnd"):
        assert event not in data["hooks"]  # emptied lists are dropped


def test_uninstall_when_never_installed(tmp_path: Path):
    settings = tmp_path / "settings.json"
    assert uninstall(settings, tmp_path / "ow-bridge") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_installer.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `install.py`**

```python
"""Register the bridge hooks in ~/.claude/settings.json — merge, never clobber.

The hook script is COPIED to <bridge_dir>/hook.py and the copy is what gets
registered, so the hook keeps working if the repo moves or the package updates.
Our entries are identified by the "ow-bridge/hook.py" marker in the command:
install is idempotent, and uninstall removes exactly what install added.

Usage: python -m coworker.claude_bridge.install [--uninstall]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

_EVENTS = ("Stop", "Notification", "SessionEnd")
_MARKER = "ow-bridge/hook.py"


def _load(settings_path: Path) -> dict:
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _is_ours(entry: dict) -> bool:
    return any(
        _MARKER in (hook.get("command") or "")
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict)
    )


def install(
    settings_path: Path, bridge_dir: Path, *, python: Optional[str] = None
) -> list[str]:
    bridge_dir.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name("hook_script.py")
    hook_path = bridge_dir / "hook.py"
    shutil.copyfile(source, hook_path)

    data = _load(settings_path)
    if settings_path.exists():
        backup = settings_path.with_name(settings_path.name + ".ow-backup")
        if not backup.exists():
            shutil.copyfile(settings_path, backup)

    interpreter = python or sys.executable
    command = f'"{interpreter}" "{hook_path}"'
    hooks = data.setdefault("hooks", {})
    registered: list[str] = []
    for event in _EVENTS:
        entries = hooks.setdefault(event, [])
        entries[:] = [e for e in entries if not (isinstance(e, dict) and _is_ours(e))]
        entries.append({"hooks": [{"type": "command", "command": command}]})
        registered.append(event)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return registered


def uninstall(settings_path: Path, bridge_dir: Path) -> list[str]:
    data = _load(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []
    removed: list[str] = []
    for event in _EVENTS:
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not (isinstance(e, dict) and _is_ours(e))]
        if len(kept) != len(entries):
            removed.append(event)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if removed:
        settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        (bridge_dir / "hook.py").unlink()
    except OSError:
        pass
    return removed


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coworker.claude_bridge.install",
        description="Install the Judith bridge hooks into Claude Code.",
    )
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    settings = Path.home() / ".claude" / "settings.json"
    bridge = Path.home() / ".claude" / "ow-bridge"
    if args.uninstall:
        removed = uninstall(settings, bridge)
        print(f"removed hooks: {', '.join(removed) or 'none'}")
    else:
        registered = install(settings, bridge)
        print(f"registered hooks: {', '.join(registered)}")
        print("restart open Claude Code sessions to pick them up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_installer.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add coworker/claude_bridge/install.py tests/test_claude_bridge_installer.py
git commit -m "Install the bridge hooks into Claude Code settings, reversibly"
```

---

### Task 7: Server wiring + full suite + smoke

**Files:**
- Modify: `coworker/server/manager.py` (init near line 202 where `self.scheduler` is built; `start_gateway()` at ~2349)
- Test: full suite + manual smoke

**Interfaces:**
- Consumes (Task 3): `BridgeWatcher`, `ConnectorNotifier`.
- Produces: the watcher runs whenever the gateway is up, macOS only.

- [ ] **Step 1: Wire the watcher into the manager**

In `coworker/server/manager.py` init, right after `self.scheduler = Scheduler(...)`:

```python
        # Claude Code bridge watcher (macOS): notifies watched terminal sessions'
        # owners on WhatsApp when a session finishes. File-registry driven; the
        # actual sends reuse the stateless connector senders.
        self.bridge_watcher = None
        if sys.platform == "darwin":
            from ..claude_bridge.watcher import BridgeWatcher, ConnectorNotifier

            self.bridge_watcher = BridgeWatcher(
                Path.home() / ".claude" / "ow-bridge",
                ConnectorNotifier(self.secrets),
            )
```

(Check the file's existing imports: `sys` and `Path` — add whichever is missing.)

In `start_gateway()`, right after `self.scheduler.start()`:

```python
        if self.bridge_watcher is not None:
            self.bridge_watcher.start()
```

- [ ] **Step 2: Run the full suite**

Run: `source .venv/bin/activate && pytest -q`
Expected: green except pre-existing known failures (`TESTE-MODULOS.md`); count ≥ phase-1's 1165 plus the new tests.

- [ ] **Step 3: Manual smoke (real machine, the only non-hermetic step)**

```bash
source .venv/bin/activate
python -m coworker.claude_bridge.install
cat ~/.claude/settings.json | python -c "import json,sys; d=json.load(sys.stdin); print(list(d['hooks']))"
# then, in any OTHER live Claude Code session, run a trivial turn and check:
ls ~/.claude/ow-bridge/sessions/
python -c "
from pathlib import Path
from coworker.claude_bridge.registry import read_sessions
for s in read_sessions(Path.home() / '.claude' / 'ow-bridge'):
    print(s.session_id[:8], s.status, s.cwd)
"
```

Expected: hooks registered; after a turn ends in another session, its state file shows `status=idle` with the right cwd.

- [ ] **Step 4: Commit**

```bash
git add coworker/server/manager.py
git commit -m "Start the bridge watcher with the gateway on macOS"
```

---

## Out of scope (from the spec)

- Remote approval of permission prompts (phase 3).
- POST/webhook transport to the sidecar.
- Watching from platforms other than the chat the request came from.
