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


def test_assistant_resolves_to_its_own_agent():
    """Not a tautology: a persona whose id is missing from the registry falls back to
    the DEFAULT persona, so `get_agent("assistant")` silently returns Cowork — same
    object shape, opposite behaviour. That is exactly what happened while this branch
    was unmerged, and only reading the prompt revealed it."""
    from coworker.agents import get_agent

    a = get_agent("assistant")
    assert a.name == "assistant"
    assert a.system_prompt.startswith("You are the user's assistant")


def test_assistant_pairs_connectors_with_no_workspace():
    """The whole point of the persona. Connectors without a workspace means it can reach
    the mailbox and has nowhere to write a file even if the model wanted to."""
    reg = PersonaRegistry()
    a = reg.agent("assistant")
    assert a.connectors is True
    assert a.needs_workspace is False
    assert a.messaging is True


def test_assistant_actually_reaches_a_connected_account(tmp_path):
    """`connectors=True` is a request, not a result — build_engine reads that flag to
    decide whether to load the integration toolset at all. Going through build_engine
    proves the request is honoured; asserting the flag alone would pass even if the
    wiring were removed.

    Gmail stands in for "any connected account": the persona exists so a coworker can
    read your mail and answer on screen, and that is the tool it needs to do it."""
    from coworker.agent import build_engine
    from coworker.secrets import SecretStore

    store = SecretStore(tmp_path / "secrets.json")
    store.put("gmail:default", {"access_token": "t", "account": "me@example.com"})

    engine = build_engine(
        agent=PersonaRegistry().agent("assistant"),
        workspace=None,  # the persona has none, and must build anyway
        secrets=store,
    )
    names = set(engine.registry.names())
    assert any(n.startswith("gmail_") for n in names), sorted(names)[:12]
    # The other half of the persona's contract, on the SAME built toolset.
    for forbidden in ("write_file", "apply_patch", "run_shell"):
        assert forbidden not in names


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
    # The explicit contract, not the absence of one Cowork phrase: any instruction to
    # save, write or produce a file would make this a second Cowork.
    assert "no file or shell access" in prompt
    assert "never offer to save something to a file" in prompt
    for promise in ("write_file", "save it to", "produce a deliverable"):
        assert promise not in prompt


def test_assistant_ships_disabled_until_the_user_enables_it(tmp_path):
    """New personas don't appear in the picker unannounced; the user turns them on in
    Settings ▸ Personas. Pinned because the persona is useless if it can't be found."""
    state = tmp_path / "personas.json"
    reg = PersonaRegistry(state_path=state)
    assert reg.is_enabled("assistant") is False
    assert "assistant" not in [p["name"] for p in reg.sidebar()]

    reg.set_enabled("assistant", True)
    reg.set_surfaced("assistant", True)
    assert "assistant" in [p["name"] for p in reg.sidebar()]

    # Reopened from disk: asserting on the same object would pass even if save() were
    # broken, and the user's choice has to survive an app restart to mean anything.
    reopened = PersonaRegistry(state_path=state)
    assert reopened.is_enabled("assistant") is True
    assert "assistant" in [p["name"] for p in reopened.sidebar()]
