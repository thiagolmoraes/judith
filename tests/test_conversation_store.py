"""ConversationStore: session id path-traversal hardening.

A session id becomes a filename ("<id>.jsonl"); ids arrive from client-controlled
surfaces (the /ws/session/{id} route, REST paths), so a crafted id must never let a
write or read escape the conversations/ directory.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from coworker.conversations import ConversationStore, is_safe_session_id
from coworker.sessions import SessionRecord


def test_is_safe_session_id():
    # Every id shape the app actually generates is accepted.
    for ok in (
        "0123456789abcdef0123456789abcdef",  # uuid4().hex
        "abc123def456",  # uuid4().hex[:12]
        "__run__run-abcdef1234",  # automation run thread
        "__task__task-0123456789",  # automation task thread
    ):
        assert is_safe_session_id(ok), ok
    # Anything that could escape a single path component is rejected.
    for bad in (
        "../evil",
        "../../etc/passwd",
        "a/b",
        "a\\b",
        "..",
        ".",
        "with space",
        "dot.dot",
        "",
        "x" * 129,  # over the length cap
    ):
        assert not is_safe_session_id(bad), bad


def test_save_rejects_traversal_id_without_writing_outside(tmp_path):
    """The verified vuln: saving a record with '../evil' used to create 'evil.jsonl'
    OUTSIDE the conversations dir. It must raise and write nothing."""
    store = ConversationStore(tmp_path / "state")
    rec = SessionRecord(
        session_id="../evil",
        workspace=str(tmp_path),
        model="m",
        mode="interactive",
        messages=[{"role": "user", "content": "hi"}],
    )
    with pytest.raises(ValueError):
        store.save(rec)
    # Nothing landed outside conversations/ (the previous behavior wrote here).
    assert not (tmp_path / "state" / "evil.jsonl").exists()
    assert list((tmp_path / "state" / "conversations").glob("*.jsonl")) == []


def test_load_of_unknown_or_unsafe_id_is_none_not_crash(tmp_path):
    store = ConversationStore(tmp_path / "state")
    # A normal missing id: no DB row, returns None (never touches the filesystem).
    assert store.load("deadbeef") is None
    # An unsafe id also has no DB row, so load short-circuits to None before any file IO.
    assert store.load("../evil") is None


def test_round_trip_with_valid_id_still_works(tmp_path):
    store = ConversationStore(tmp_path / "state")
    rec = SessionRecord(
        session_id="abc123def456",
        workspace=str(tmp_path),
        model="m",
        mode="interactive",
        messages=[{"role": "user", "content": "hello"}],
    )
    store.save(rec)
    loaded = store.load("abc123def456")
    assert loaded is not None
    assert loaded.messages[0]["content"] == "hello"
    assert (tmp_path / "state" / "conversations" / "abc123def456.jsonl").is_file()


# -- legacy rows with an unsafe id -----------------------------------------------
#
# Ids were not checked before. A row like "../evil" can already sit in the index.
# `_file()` raises on it, so every path that touched the file for that row (startup
# backfill, load, delete) raised too. The store never opened and the row could not
# be removed. Rule now: unsafe id means no file. The row stays readable and
# deletable; nothing is ever written for it.


def _insert_legacy_row(base, sid: str, messages: list[dict] | None = None) -> None:
    """Write a row straight into the index, the way a pre-check store left it."""
    conn = sqlite3.connect(base / "coworker.db")
    try:
        conn.execute(
            "INSERT INTO sessions (session_id, workspace, model, mode, messages) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, "/w", "m", "interactive", json.dumps(messages) if messages else None),
        )
        conn.commit()
    finally:
        conn.close()


def test_store_boots_with_a_legacy_unsafe_id_row(tmp_path):
    base = tmp_path / "state"
    ConversationStore(base).close()
    _insert_legacy_row(base, "../evil", [{"role": "user", "content": "hi"}])

    store = ConversationStore(base)  # startup backfill must skip the row, not raise

    assert "../evil" in {r.session_id for r in store.list()}
    assert list(tmp_path.rglob("*.jsonl")) == []


def test_load_of_legacy_unsafe_id_reads_the_blob_and_writes_no_file(tmp_path):
    base = tmp_path / "state"
    store = ConversationStore(base)
    # Out-of-order tool result: the pairing repair changes the list, and that is the
    # branch that rewrites the log. For an unsafe id it must not.
    blob = [
        {"role": "assistant", "tool_calls": [{"id": "c1", "name": "x", "arguments": {}}]},
        {"role": "user", "content": "next"},
        {"role": "tool", "tool_call_id": "c1", "content": "r"},
    ]
    _insert_legacy_row(base, "../evil", blob)

    rec = store.load("../evil")

    assert rec is not None
    assert [m["role"] for m in rec.messages] == ["assistant", "tool", "user"]
    assert list(tmp_path.rglob("*.jsonl")) == []
    assert list(tmp_path.rglob("*.tmp")) == []


def test_delete_of_legacy_unsafe_id_removes_the_row(tmp_path):
    base = tmp_path / "state"
    store = ConversationStore(base)
    _insert_legacy_row(base, "../evil")

    assert store.delete("../evil") is True

    assert store.load("../evil") is None
    assert "../evil" not in {r.session_id for r in store.list()}


def test_save_with_unsafe_id_still_raises_even_when_a_row_exists(tmp_path):
    base = tmp_path / "state"
    store = ConversationStore(base)
    _insert_legacy_row(base, "../evil")
    rec = SessionRecord(
        session_id="../evil",
        workspace="/w",
        model="m",
        mode="interactive",
        messages=[{"role": "user", "content": "hi"}],
    )

    with pytest.raises(ValueError):
        store.save(rec)

    assert list(tmp_path.rglob("*.jsonl")) == []
