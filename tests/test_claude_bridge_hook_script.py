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
    payload = _payload(
        "Notification", message="Claude needs permission to run: git push"
    )
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
