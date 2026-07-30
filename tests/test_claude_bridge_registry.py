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
    (sessions / "noid.json").write_text(
        json.dumps({"status": "idle"}), encoding="utf-8"
    )
    _write_state(tmp_path, "ok")
    assert [s.session_id for s in read_sessions(tmp_path)] == ["ok"]


def test_read_sessions_tolerates_bad_fields(tmp_path: Path):
    _write_state(
        tmp_path, "odd", pid="not-an-int", updated_at="not-a-date", transcript_path=None
    )
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
