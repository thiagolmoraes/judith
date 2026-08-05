"""Persona × tools matrix for build_engine.

build_engine decides which tools each persona gets from a pile of family/flag
conditionals. Nothing pinned that matrix directly — a regression (Code silently
gaining scheduling, Cowork losing memory) only surfaced through distant
integration tests, if at all. One table per persona locks it down.

Deterministic surface only: send_message/connector tools depend on configured
channels (none in tests) and the Claude bridge is Darwin-only, so those stay out
of the expected sets and the bridge is asserted separately.
"""

from __future__ import annotations

import sys

import pytest

from coworker.agents import chat_agent, code_agent, cowork_agent, myhelper_agent
from coworker.agent import build_engine
from coworker.automation import TaskStore
from coworker.memory import SQLiteMemoryStore
from coworker.selfwake import WakeStore


class _StubProvider:
    def complete(self, **kwargs):  # pragma: no cover - not invoked
        raise NotImplementedError

    def capabilities(self, model):  # pragma: no cover
        raise NotImplementedError


async def _asker(payload, timeout):  # pragma: no cover - registration only
    return None


def _engine(agent, tmp_path):
    return build_engine(
        agent=agent,
        workspace=tmp_path,
        provider=_StubProvider(),
        memory_store=SQLiteMemoryStore(tmp_path / "mem.db"),
        task_store=TaskStore(tmp_path / "auto.db"),
        wake_store=WakeStore(tmp_path / "wakes.json"),
        session_id="s1",
        question_asker=_asker,
    )


# Tools whose registration is a family/flag decision in build_engine.
GATED = {
    "explore",  # code family only (fans research out to subagents)
    "create_scheduled_task",  # knowledge family + workspace + task_store
    "sleep_until",  # knowledge family + wake_store + session_id
    "request_directory",  # knowledge family with roots
    "remember",  # memory store wired
    "ask_user",  # question_asker wired
    "propose_plan",  # always (plan-mode exit door)
    "web_search",  # always
    "web_fetch",
}

MATRIX = [
    (
        code_agent,
        # Code explores with subagents; it deliberately gets no scheduling,
        # self-wake, or directory requests.
        {
            "explore",
            "remember",
            "ask_user",
            "propose_plan",
            "web_search",
            "web_fetch",
        },
    ),
    (
        cowork_agent,
        {
            "create_scheduled_task",
            "sleep_until",
            "request_directory",
            "remember",
            "ask_user",
            "propose_plan",
            "web_search",
            "web_fetch",
        },
    ),
    (
        myhelper_agent,
        {
            "create_scheduled_task",
            "sleep_until",
            "request_directory",
            "remember",
            "ask_user",
            "propose_plan",
            "web_search",
            "web_fetch",
        },
    ),
]


@pytest.mark.parametrize(("factory", "expected"), MATRIX, ids=lambda p: getattr(p, "__name__", ""))
def test_persona_gets_exactly_its_gated_tools(factory, expected, tmp_path):
    engine = _engine(factory(), tmp_path)
    try:
        names = set(engine.registry.names())
        assert names & GATED == expected
    finally:
        if getattr(engine, "executor", None) is not None:
            engine.executor.close()


def test_chat_without_workspace_keeps_the_floor_only(tmp_path):
    engine = build_engine(
        agent=chat_agent(),
        provider=_StubProvider(),
        memory_store=SQLiteMemoryStore(tmp_path / "mem.db"),
        task_store=TaskStore(tmp_path / "auto.db"),
        wake_store=WakeStore(tmp_path / "wakes.json"),
        session_id="s1",
        question_asker=_asker,
    )
    names = set(engine.registry.names())
    # No workspace → no scheduling and no directory requests, even for knowledge
    # family; self-wake and memory don't need a workspace.
    assert names & GATED == {
        "sleep_until",
        "remember",
        "ask_user",
        "propose_plan",
        "web_search",
        "web_fetch",
    }


@pytest.mark.skipif(sys.platform != "darwin", reason="bridge is AppleScript-driven")
def test_messaging_personas_get_the_claude_bridge_on_darwin(tmp_path):
    engine = _engine(cowork_agent(), tmp_path)
    try:
        names = set(engine.registry.names())
        assert {"find_claude_sessions", "read_claude_transcript"} <= names
    finally:
        engine.executor.close()

    plain = _engine(code_agent(), tmp_path)
    try:
        assert "find_claude_sessions" not in set(plain.registry.names())
    finally:
        plain.executor.close()
