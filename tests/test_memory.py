"""P4 gate tests — memory store + sessions."""

from __future__ import annotations

import aisuite as ai
from coworker.conversations import ConversationStore
from coworker.memory import Scope, SQLiteMemoryStore, format_memories, memory_tools
from coworker.sessions import SessionRecord
from coworker.tools import ToolRegistry


def _store(tmp_path):
    return SQLiteMemoryStore(tmp_path / "mem.db")


# -- memory store ---------------------------------------------------------------


def test_memory_round_trip(tmp_path):
    store = _store(tmp_path)
    item = store.add(
        "prefers tabs over spaces", scope=Scope.WORKSPACE, workspace="/proj"
    )
    assert store.get(item.id).content == "prefers tabs over spaces"
    assert [m.content for m in store.list(workspace="/proj")] == [
        "prefers tabs over spaces"
    ]


def test_workspace_scope_isolation(tmp_path):
    store = _store(tmp_path)
    store.add("A secret", scope=Scope.WORKSPACE, workspace="/proj/a")
    assert store.list(workspace="/proj/b") == []
    assert len(store.list(workspace="/proj/a")) == 1


def test_global_scope_visible_regardless_of_workspace(tmp_path):
    store = _store(tmp_path)
    store.add("use 2-space indent everywhere", scope=Scope.GLOBAL)
    assert len(store.list(scope=Scope.GLOBAL)) == 1


def test_memory_listable_and_editable(tmp_path):
    store = _store(tmp_path)
    item = store.add("old note", scope=Scope.WORKSPACE, workspace="/proj")
    updated = store.update(item.id, "new note")
    assert updated.content == "new note"
    assert store.delete(item.id) is True
    assert store.get(item.id) is None


def test_update_stamps_updated_at(tmp_path):
    store = _store(tmp_path)
    item = store.add("v1", scope=Scope.GLOBAL)
    assert item.updated_at is None  # never revised → reads as originally written
    revised = store.update(item.id, "v2")
    assert revised.updated_at is not None


def test_legacy_schema_migrates_in_place(tmp_path):
    import sqlite3

    # A database created by the old schema: key/session_id columns, no updated_at.
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE memories ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, key TEXT, "
        "content TEXT NOT NULL, workspace TEXT, session_id TEXT, "
        "created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute("INSERT INTO memories (scope, content) VALUES ('global', 'old fact')")
    conn.commit()
    conn.close()

    store = SQLiteMemoryStore(db)
    items = store.list(scope=Scope.GLOBAL)
    assert [m.content for m in items] == ["old fact"]
    # updated_at arrives via ALTER TABLE; inserts ignore the inert legacy columns.
    assert store.update(items[0].id, "new fact").updated_at is not None
    assert store.add("fresh", scope=Scope.GLOBAL).content == "fresh"


def test_format_memories_shows_ids(tmp_path):
    store = _store(tmp_path)
    item = store.add("fact one", workspace="/proj")
    rendered = format_memories(store.list(workspace="/proj"))
    assert "fact one" in rendered and "Known memories" in rendered
    assert f"[#{item.id}]" in rendered  # ids let the agent update/forget


def test_format_memories_notes_omitted_entries(tmp_path):
    store = _store(tmp_path)
    store.add("kept", workspace="/proj")
    rendered = format_memories(store.list(workspace="/proj"), omitted=7)
    assert "7 older memories not shown" in rendered
    assert "memory_search" in rendered
    # No note when nothing was left out.
    assert "not shown" not in format_memories(store.list(workspace="/proj"))


# -- search ---------------------------------------------------------------------


def test_search_matches_substring_newest_first(tmp_path):
    store = _store(tmp_path)
    old = store.add("deploy uses blue-green strategy", scope=Scope.GLOBAL)
    new = store.add("deploy window is Friday 6pm", scope=Scope.GLOBAL)
    found = store.search("deploy", scope=Scope.GLOBAL)
    assert [m.id for m in found] == [new.id, old.id]


def test_search_respects_workspace_isolation(tmp_path):
    store = _store(tmp_path)
    store.add("port 8080 in use", scope=Scope.WORKSPACE, workspace="/proj/a")
    assert store.search("port", scope=Scope.WORKSPACE, workspace="/proj/b") == []
    assert len(store.search("port", scope=Scope.WORKSPACE, workspace="/proj/a")) == 1


def test_search_escapes_like_wildcards(tmp_path):
    store = _store(tmp_path)
    store.add("progress at 100% done", scope=Scope.GLOBAL)
    store.add("totally unrelated", scope=Scope.GLOBAL)
    # A literal % must not turn into match-everything.
    assert len(store.search("100%", scope=Scope.GLOBAL)) == 1
    assert store.search("100_", scope=Scope.GLOBAL) == []


def test_search_negative_limit_returns_nothing(tmp_path):
    store = _store(tmp_path)
    store.add("anything", scope=Scope.GLOBAL)
    # SQLite reads LIMIT -1 as "no limit" — a negative limit must mean nothing.
    assert store.search("anything", scope=Scope.GLOBAL, limit=-1) == []


def test_recent_and_count_fetch_bounded(tmp_path):
    store = _store(tmp_path)
    ids = [
        store.add(f"note {i}", scope=Scope.WORKSPACE, workspace="/proj").id
        for i in range(5)
    ]
    store.add("elsewhere", scope=Scope.WORKSPACE, workspace="/other")
    recent = store.recent(scope=Scope.WORKSPACE, workspace="/proj", limit=2)
    assert [m.id for m in recent] == [ids[-1], ids[-2]]  # newest first, capped
    assert store.count(scope=Scope.WORKSPACE, workspace="/proj") == 5
    assert store.recent(scope=Scope.WORKSPACE, workspace="/proj", limit=-3) == []


def test_memory_search_tool_covers_global_and_workspace(tmp_path):
    store = _store(tmp_path)
    store.add("prefers dark theme", scope=Scope.GLOBAL)
    store.add("this repo pins node 20", scope=Scope.WORKSPACE, workspace="/proj")
    store.add("other repo pins node 18", scope=Scope.WORKSPACE, workspace="/other")
    reg = ToolRegistry()
    reg.register_all(memory_tools(store, workspace="/proj"))
    result = reg.execute("memory_search", {"query": "node"})
    contents = [r["content"] for r in result["results"]]
    assert contents == ["this repo pins node 20"]  # other workspace stays invisible
    themed = reg.execute("memory_search", {"query": "theme"})
    assert themed["count"] == 1


# -- remember tool --------------------------------------------------------------


def test_remember_tool_persists(tmp_path):
    store = _store(tmp_path)
    reg = ToolRegistry()
    reg.register_all(memory_tools(store, workspace="/proj"))
    assert "remember" in reg.names()

    result = reg.execute("remember", {"content": "deploys on Fridays are banned"})
    assert result["saved"] is True
    assert any(
        m.content == "deploys on Fridays are banned"
        for m in store.list(workspace="/proj")
    )


def test_memory_update_and_forget_tools(tmp_path):
    store = _store(tmp_path)
    reg = ToolRegistry()
    reg.register_all(memory_tools(store, workspace="/proj"))
    assert {"remember", "memory_update", "memory_forget"} <= set(reg.names())

    saved = reg.execute("remember", {"content": "uses npm"})
    updated = reg.execute(
        "memory_update", {"memory_id": saved["id"], "content": "uses pnpm, not npm"}
    )
    assert updated["updated"] is True
    assert store.get(saved["id"]).content == "uses pnpm, not npm"

    gone = reg.execute("memory_forget", {"memory_id": saved["id"]})
    assert gone["deleted"] is True
    assert store.get(saved["id"]) is None


def test_memory_update_and_forget_unknown_id(tmp_path):
    store = _store(tmp_path)
    reg = ToolRegistry()
    reg.register_all(memory_tools(store, workspace="/proj"))
    assert (
        "no memory"
        in reg.execute("memory_update", {"memory_id": 99, "content": "x"})["error"]
    )
    assert "no memory" in reg.execute("memory_forget", {"memory_id": 99})["error"]


# -- sessions -------------------------------------------------------------------


def test_session_save_and_resume(tmp_path):
    store = ConversationStore(tmp_path)
    messages = [
        {"role": "system", "content": "be helpful"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    store.save(
        SessionRecord(
            session_id="s1",
            workspace="/proj",
            model="gpt-5.5",
            mode="interactive",
            messages=messages,
        )
    )
    loaded = store.load("s1")
    assert loaded is not None
    assert loaded.messages == messages
    assert loaded.model == "gpt-5.5"
    # messages live in an append-only jsonl, not the index db
    assert (tmp_path / "conversations" / "s1.jsonl").exists()


class _StubProvider:
    def complete(self, **kwargs):  # pragma: no cover - not invoked
        raise NotImplementedError

    def capabilities(self, model):  # pragma: no cover
        raise NotImplementedError


def test_build_code_engine_injects_memory(tmp_path):
    from coworker.agent import build_code_engine

    workspace = str(tmp_path.resolve())
    store = SQLiteMemoryStore(tmp_path / "mem.db")
    store.add(
        "always run black before committing", scope=Scope.WORKSPACE, workspace=workspace
    )

    engine = build_code_engine(
        workspace=tmp_path, provider=_StubProvider(), memory_store=store
    )
    try:
        assert {"remember", "memory_update", "memory_forget"} <= set(
            engine.registry.names()
        )
        assert engine.messages[0]["role"] == "system"
        assert "always run black" in engine.messages[0]["content"]
        # when-to-remember guidance rides along with the tools
        assert "memory_update" in engine.messages[0]["content"]
        assert (
            "Don't save what the repo already records" in engine.messages[0]["content"]
        )
    finally:
        engine.executor.close()


def test_build_engine_caps_injected_memories_at_newest(tmp_path):
    from coworker.agent import _MEMORY_INJECT_CAP, build_code_engine

    workspace = str(tmp_path.resolve())
    store = SQLiteMemoryStore(tmp_path / "mem.db")
    total = _MEMORY_INJECT_CAP + 5
    for i in range(total):
        store.add(f"fact number {i:03d}", scope=Scope.WORKSPACE, workspace=workspace)

    engine = build_code_engine(
        workspace=tmp_path, provider=_StubProvider(), memory_store=store
    )
    try:
        system = engine.messages[0]["content"]
        # Oldest 5 stay out; the newest cap-full is in; the note says how many more exist.
        assert "fact number 004" not in system
        assert "fact number 005" in system
        assert f"fact number {total - 1:03d}" in system
        assert "5 older memories not shown" in system
        assert "memory_search" in engine.registry.names()
    finally:
        engine.executor.close()


def test_session_append_only_and_list(tmp_path):
    store = ConversationStore(tmp_path)
    store.save(
        SessionRecord(
            "s1", "/proj", "gpt-5.5", "interactive", [{"role": "user", "content": "a"}]
        )
    )
    store.save(
        SessionRecord(
            "s1",
            "/proj",
            "gpt-5.5",
            "interactive",
            [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}],
        )
    )
    loaded = store.load("s1")
    assert len(loaded.messages) == 2  # appended, not duplicated
    listed = store.list(workspace="/proj")
    assert len(listed) == 1
    assert listed[0].message_count == 2
    assert listed[0].title == "a"
