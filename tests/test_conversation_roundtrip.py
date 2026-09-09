"""ConversationStore: disk and memory must agree after load().

save() appends by line count: it counts the non-empty lines on disk and writes
`record.messages[count:]`. load() can hand back a list of a different size than the
disk. Placeholder repair inserts a message (list grows). A corrupt line is skipped
(list shrinks). Either way the next save() appends from the wrong offset: messages
duplicate, or the first new one is lost. load() must leave the disk matching what it
returned.
"""

from __future__ import annotations

import json

from coworker.conversations import ConversationStore
from coworker.sessions import SessionRecord

SID = "abc123def456"


# -- helpers -------------------------------------------------------------------

def _user(text: str = "continue") -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _assistant_with_calls(call_id: str = "c1") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": "run_shell", "arguments": "{}"}}
        ],
    }


def _tool_result(call_id: str = "c1") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": '{"ok": true}'}


def _record(sid: str, messages: list[dict]) -> SessionRecord:
    return SessionRecord(
        session_id=sid, workspace="/tmp", model="m", mode="interactive", messages=messages
    )


def _store(tmp_path) -> ConversationStore:
    return ConversationStore(tmp_path / "state")


def _jsonl(tmp_path, sid: str):
    return tmp_path / "state" / "conversations" / f"{sid}.jsonl"


def _lines(path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _roles(messages: list[dict]) -> list[str]:
    return [m["role"] for m in messages]


# -- placeholder repair grows the list ------------------------------------------

def test_placeholder_repair_does_not_duplicate_on_the_next_save(tmp_path):
    """A dangling tool call gets a placeholder on load (list of 4 from 3 lines). The
    next turn's save must append only the new messages, not replay the tail."""
    store = _store(tmp_path)
    store.save(_record(SID, [_user("go"), _assistant_with_calls("c1"), _user("next")]))

    rec = store.load(SID)
    assert _roles(rec.messages) == ["user", "assistant", "tool", "user"]

    turn = [_user("more"), _assistant("done")]
    store.save(_record(SID, rec.messages + turn))

    again = store.load(SID)
    assert again.messages == rec.messages + turn
    assert len(again.messages) == 6
    assert len(_lines(_jsonl(tmp_path, SID))) == 6


# -- a dropped corrupt line shrinks the list -------------------------------------

def test_dropped_corrupt_line_does_not_eat_the_next_message(tmp_path):
    """load() skips a corrupt line (list of 2 from 3 lines). Without a rewrite the next
    save would count 3 on disk and skip the first new message."""
    store = _store(tmp_path)
    m0, m1 = _user("m0"), _user("m1")
    store.save(_record(SID, [m0, m1]))
    with open(_jsonl(tmp_path, SID), "a", encoding="utf-8") as f:
        f.write('{"role": "user", "cont\n')

    rec = store.load(SID)
    assert [m["content"] for m in rec.messages] == ["m0", "m1"]

    store.save(_record(SID, rec.messages + [_user("new")]))

    again = store.load(SID)
    assert [m["content"] for m in again.messages] == ["m0", "m1", "new"]
    assert store._count(SID) == 3


# -- a torn line with no newline -------------------------------------------------

def test_torn_trailing_line_without_newline_cannot_swallow_the_next_append(tmp_path):
    """A write cut mid-record leaves a line with no newline. The engine still holds the
    message it believed it saved (m2), so the torn line occupies its slot in the count.
    The next append must start on a fresh line. Otherwise the new record is glued to
    the torn one and both vanish on load."""
    store = _store(tmp_path)
    m0, m1, m2 = _user("m0"), _user("m1"), _user("m2")
    store.save(_record(SID, [m0, m1]))
    with open(_jsonl(tmp_path, SID), "a", encoding="utf-8") as f:
        f.write('{"role": "user", "cont')

    # No load in between: the cached engine appends its next message straight away.
    store.save(_record(SID, [m0, m1, m2, _user("new")]))

    rec = store.load(SID)
    assert [m["content"] for m in rec.messages] == ["m0", "m1", "new"]


# -- legacy inline blob ----------------------------------------------------------

def test_legacy_blob_is_materialised_on_load_and_appends_cleanly(tmp_path):
    """A row that still stores messages inline gets repaired on load. The repaired list
    must land on disk as a .jsonl, or save() migrates the raw blob underneath it and the
    offsets drift."""
    store = _store(tmp_path)
    store.save(_record(SID, [_user("seed")]))
    jsonl = _jsonl(tmp_path, SID)
    jsonl.unlink()
    blob = json.dumps([_user("go"), _assistant_with_calls("c1"), _user("next")])
    store._conn.execute(
        "UPDATE sessions SET messages = ? WHERE session_id = ?", (blob, SID)
    )
    store._conn.commit()

    rec = store.load(SID)
    assert _roles(rec.messages) == ["user", "assistant", "tool", "user"]
    assert jsonl.exists()
    assert len(_lines(jsonl)) == 4

    turn = [_user("more"), _assistant("done")]
    store.save(_record(SID, rec.messages + turn))

    again = store.load(SID)
    assert again.messages == rec.messages + turn
    assert len(again.messages) == 6


# -- idempotence -----------------------------------------------------------------

def test_well_formed_log_is_not_rewritten_on_load(tmp_path, monkeypatch):
    """Nothing to repair and nothing dropped: load() must leave the file alone."""
    store = _store(tmp_path)
    store.save(
        _record(SID, [_user("go"), _assistant_with_calls("c1"), _tool_result("c1"), _user("ok")])
    )
    jsonl = _jsonl(tmp_path, SID)
    before = jsonl.read_bytes()

    rewrites: list[str] = []
    monkeypatch.setattr(store, "_rewrite", lambda sid, messages: rewrites.append(sid))

    rec = store.load(SID)

    assert len(rec.messages) == 4
    assert rewrites == []
    assert jsonl.read_bytes() == before


def test_empty_never_saved_session_does_not_grow_a_file(tmp_path):
    """A row with no .jsonl and no blob loads as empty. No repair, so no file appears."""
    store = _store(tmp_path)
    store._conn.execute(
        "INSERT INTO sessions (session_id, workspace, model, mode, title, n_msgs) "
        "VALUES (?, '/tmp', 'm', 'interactive', 't', 0)",
        (SID,),
    )
    store._conn.commit()

    rec = store.load(SID)

    assert rec.messages == []
    assert not _jsonl(tmp_path, SID).exists()
