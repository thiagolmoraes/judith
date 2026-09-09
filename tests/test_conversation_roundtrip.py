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


def _assistant_with_calls(*call_ids: str) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": "run_shell", "arguments": "{}"}}
            for cid in (call_ids or ("c1",))
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


def _write_compact(path, messages: list[dict]) -> None:
    """Write the log by hand with compact separators. The store rewrites with the
    default ones, so a rewrite of identical messages still shows up as a byte change.
    Without this a needless rewrite is invisible to a bytes comparison."""
    path.write_text(
        "".join(json.dumps(m, separators=(",", ":")) + "\n" for m in messages),
        encoding="utf-8",
    )


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

TORN_INSIDE_AN_EMOJI = b'{"role": "user", "content": "ol\xc3\xa1 \xf0\x9f\x98'


def _append_raw(tmp_path, data: bytes) -> None:
    with open(_jsonl(tmp_path, SID), "ab") as f:
        f.write(data)


def test_torn_trailing_line_without_newline_cannot_swallow_the_next_append(tmp_path):
    """A write cut mid-record leaves a line with no newline. The engine still holds
    the message it believed it saved (m2). Counting the torn line as m2's slot would
    append only m3, and m2 is gone for good on the next load. The engine's list is
    the truth: a torn tail means the whole log is written again from it."""
    store = _store(tmp_path)
    m0, m1, m2, m3 = _user("m0"), _user("m1"), _user("m2"), _user("m3")
    store.save(_record(SID, [m0, m1]))
    _append_raw(tmp_path, b'{"role": "user", "cont')

    # No load in between: the cached engine saves its next turn straight away.
    store.save(_record(SID, [m0, m1, m2, m3]))

    rec = store.load(SID)
    assert [m["content"] for m in rec.messages] == ["m0", "m1", "m2", "m3"]


def test_torn_tool_call_line_is_recovered_with_its_result(tmp_path):
    """A checkpoint appends [A(tool_calls), T]. The write tears inside A and T never
    lands. Memory holds [u, A, T]; disk holds [u, A_torn]. Counting the torn line as
    A's slot appended only T. On reload A is dropped and T is a result with no call,
    which the provider rejects (400) and the pairing repair cannot fix."""
    store = _store(tmp_path)
    u, a, t = _user("go"), _assistant_with_calls("c1"), _tool_result("c1")
    store.save(_record(SID, [u]))
    _append_raw(tmp_path, json.dumps(a)[:24].encode("utf-8"))

    store.save(_record(SID, [u, a, t]))

    rec = store.load(SID)
    assert rec.messages == [u, a, t]


def test_torn_line_inside_a_multibyte_char_does_not_brick_load_or_save(tmp_path):
    """A DM ends in an emoji, four bytes in UTF-8. A write cut inside it leaves bytes
    that are not valid UTF-8. A strict text read raised UnicodeDecodeError from
    load(), so the session could not be opened on any surface."""
    store = _store(tmp_path)
    m0, m1 = _user("m0"), _user("m1")
    store.save(_record(SID, [m0, m1]))
    _append_raw(tmp_path, TORN_INSIDE_AN_EMOJI)

    rec = store.load(SID)
    assert [m["content"] for m in rec.messages] == ["m0", "m1"]

    store.save(_record(SID, rec.messages + [_user("new")]))

    again = store.load(SID)
    assert [m["content"] for m in again.messages] == ["m0", "m1", "new"]


def test_count_tolerates_a_torn_line_inside_a_multibyte_char(tmp_path):
    """save() counts lines before it appends. The same strict read raised there too,
    so a torn emoji blocked every save of that session, not just its loads."""
    store = _store(tmp_path)
    store.save(_record(SID, [_user("m0"), _user("m1")]))
    _append_raw(tmp_path, TORN_INSIDE_AN_EMOJI)

    assert store._count(SID) == 3


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

def test_well_formed_log_is_not_rewritten_on_load(tmp_path):
    """Nothing to repair and nothing dropped: load() must leave the file alone."""
    store = _store(tmp_path)
    messages = [_user("go"), _assistant_with_calls("c1"), _tool_result("c1"), _user("ok")]
    store.save(_record(SID, messages))
    jsonl = _jsonl(tmp_path, SID)
    _write_compact(jsonl, messages)
    before = jsonl.read_bytes()

    rec = store.load(SID)

    assert len(rec.messages) == 4
    assert jsonl.read_bytes() == before


def test_well_formed_multi_call_block_is_not_rewritten_on_load(tmp_path):
    """Two calls in one assistant block, both results right behind it. The pairing
    check reads the second result as out of place (it sits at call index + 2) and
    builds a fresh list with the same content. Identity as the rewrite signal then
    rewrote the file on every load, and load runs on every inbound and inbox poll."""
    store = _store(tmp_path)
    messages = [
        _user("go"),
        _assistant_with_calls("c1", "c2"),
        _tool_result("c1"),
        _tool_result("c2"),
        _assistant("done"),
    ]
    store.save(_record(SID, messages))
    jsonl = _jsonl(tmp_path, SID)
    _write_compact(jsonl, messages)
    before = jsonl.read_bytes()

    first = store.load(SID)
    assert jsonl.read_bytes() == before

    second = store.load(SID)
    assert jsonl.read_bytes() == before
    assert first.messages == second.messages == messages


def test_repair_on_load_updates_the_index_count(tmp_path):
    """A placeholder inserted on load makes the file one line longer. The session
    list reads n_msgs from the index, so the count must follow the rewrite. Waiting
    for the next save showed a stale number until then."""
    store = _store(tmp_path)
    store.save(_record(SID, [_user("go"), _assistant_with_calls("c1"), _user("next")]))
    assert store.list()[0].message_count == 3

    rec = store.load(SID)

    assert len(rec.messages) == 4
    assert store.list()[0].message_count == 4


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


# -- delete ----------------------------------------------------------------------

def test_delete_removes_the_row_and_the_log_together(tmp_path):
    """Both halves go, and a second delete reports nothing left. load() writes now,
    so the unlink sits under the same lock as the row delete."""
    store = _store(tmp_path)
    store.save(_record(SID, [_user("go")]))
    jsonl = _jsonl(tmp_path, SID)
    assert jsonl.exists()

    assert store.delete(SID) is True

    assert store.load(SID) is None
    assert not jsonl.exists()
    assert store.delete(SID) is False
