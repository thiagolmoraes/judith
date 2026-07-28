"""Phase 1 gate — built-in personas resolve to the same toolsets as the legacy agents.

The equivalence net: routing Code/Cowork through the persona registry must yield the exact
same tools the agent builders produce, and Ops (a markdown persona) must compose the knowledge
toolset. Ties back to the Phase 0 catalog equivalence."""

from __future__ import annotations

from coworker.agents.base import AgentContext
from coworker.agents.code import code_agent
from coworker.agents.cowork import cowork_agent
from coworker.personas.registry import PersonaRegistry
from coworker.tools.todo import TodoList


def _ctx(tmp_path) -> AgentContext:
    return AgentContext(workspace=tmp_path, executor=object(), todo=TodoList())


def _names(agent, ctx) -> set:
    return {getattr(t, "__name__", "") for t in agent.build_tools(ctx)}


def test_code_persona_matches_builder(tmp_path):
    reg = PersonaRegistry()
    ctx = _ctx(tmp_path)
    assert _names(reg.agent("code"), ctx) == _names(code_agent(), ctx)
    assert reg.agent("code").family == "code"


def test_cowork_persona_matches_builder(tmp_path):
    reg = PersonaRegistry()
    ctx = _ctx(tmp_path)
    assert _names(reg.agent("cowork"), ctx) == _names(cowork_agent(), ctx)
    a = reg.agent("cowork")
    assert a.messaging and a.connectors


def test_ops_persona_composes_knowledge_toolset(tmp_path):
    reg = PersonaRegistry()
    ctx = _ctx(tmp_path)
    # Ops uses the same capability list as Cowork (files/search/shell/todo).
    assert _names(reg.agent("ops"), ctx) == _names(cowork_agent(), ctx)
    a = reg.agent("ops")
    assert a.family == "knowledge" and a.messaging and a.connectors
    assert "read_file_lines" in _names(a, ctx)  # multi-root knowledge files


def test_code_keeps_single_root_file_tools(tmp_path):
    reg = PersonaRegistry()
    names = _names(reg.agent("code"), _ctx(tmp_path))
    assert "read_file" in names and "read_file_lines" not in names
    assert "git_log" in names  # code has git; cowork/ops do not


# -- Assistant ------------------------------------------------------------------
# The gap it fills: Cowork was the only persona with connectors, and its prompt points
# at producing a deliverable — so "summarise my unread mail" produced summary.md instead
# of an answer. Chat answers on screen but has no connectors, so it can't read the mail.


def test_assistant_pairs_connectors_with_no_workspace():
    """The whole point of the persona. Connectors without a workspace means it can reach
    the mailbox and has nowhere to write a file even if the model wanted to."""
    reg = PersonaRegistry()
    a = reg.agent("assistant")
    assert a.connectors is True
    assert a.needs_workspace is False
    assert a.messaging is True


def test_assistant_has_no_file_or_shell_tools(tmp_path):
    """A workspace-less persona gets no file/shell toolset. Asserted on the built tools
    rather than the flag, so wiring a factory in later can't silently reopen the door."""
    reg = PersonaRegistry()
    names = _names(reg.agent("assistant"), _ctx(tmp_path))
    for forbidden in ("write_file", "apply_patch", "run_shell", "read_file"):
        assert forbidden not in names, f"assistant must not carry {forbidden}"


def test_assistant_prompt_directs_answers_on_screen():
    """The prompt is the fix. If it stops saying so, the persona silently becomes a
    second Cowork — which is exactly the bug it exists to correct."""
    reg = PersonaRegistry()
    prompt = reg.agent("assistant").system_prompt.lower()
    assert "answer in the conversation" in prompt
    assert "no workspace" in prompt
    # And it must not promise files.
    assert "deliverable (a memo" not in prompt


def test_assistant_ships_disabled_until_the_user_enables_it(tmp_path):
    """New personas don't appear in the picker unannounced; the user turns them on in
    Settings ▸ Personas. Pinned because the persona is useless if it can't be found."""
    reg = PersonaRegistry(state_path=tmp_path / "personas.json")
    assert reg.is_enabled("assistant") is False
    assert "assistant" not in [p["name"] for p in reg.sidebar()]

    reg.set_enabled("assistant", True)
    reg.set_surfaced("assistant", True)
    assert "assistant" in [p["name"] for p in reg.sidebar()]
