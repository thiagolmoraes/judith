"""The running flag, and what happens to messages that arrive while it is set.

Found the hard way: a WhatsApp DM reached the sidecar, the webhook answered
`{"ok": true, "handled": true}`, and nothing happened — repeatedly, for half an hour,
while I blamed the network. The session's running flag had been left set by a turn whose
cleanup never ran, so every later message was queued into a turn that would never
execute. A stuck session is indistinguishable from a busy one, the queue is invisible,
and nothing in the app can clear it.
"""

from __future__ import annotations

import pytest

from coworker.server import SessionManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    return SessionManager(workspace=tmp_path)


# -- the flag ------------------------------------------------------------------
def test_the_flag_gates_a_second_turn(manager):
    assert manager.try_mark_running("s1") is True
    assert manager.try_mark_running("s1") is False  # already running
    manager.mark_idle("s1")
    assert manager.try_mark_running("s1") is True


def test_force_idle_clears_a_stuck_flag(manager):
    """The escape hatch. The flag is in-memory and per-process, so before this there was
    no way — API, GUI or otherwise — to clear one that got stuck."""
    manager.try_mark_running("s1")
    assert manager.is_running("s1") is True

    result = manager.force_idle("s1")
    assert result["ok"] is True
    assert result["was_running"] is True
    assert manager.is_running("s1") is False
    # And the session accepts turns again.
    assert manager.try_mark_running("s1") is True


def test_force_idle_on_an_idle_session_is_a_no_op(manager):
    """Reports what it found rather than pretending: `was_running` is how a caller
    learns whether it actually unstuck anything."""
    assert manager.force_idle("never-ran") == {"ok": True, "was_running": False}


# -- what happens to a message that arrives while stuck ------------------------
async def test_a_message_to_a_busy_session_is_recorded(manager, monkeypatch):
    """It still gets steered into the live turn — that part was right. What was missing
    is any trace that it arrived, which is why a stuck session looked like silence."""
    queued: list[str] = []

    class _Engine:
        def queue_steering(self, message, source=None):
            queued.append(message)

    monkeypatch.setattr(manager, "get_engine", lambda *a, **k: _Engine())
    manager.try_mark_running("s1")

    await manager.deliver_to_session("s1", "hello from WhatsApp")
    assert queued == ["hello from WhatsApp"]


async def test_a_message_to_an_unresumable_session_is_parked(manager, monkeypatch):
    """`get_engine` returning None used to `return` — the message evaporated. Now it
    lands in the dead-letter store, which is the one place a person can find it."""
    monkeypatch.setattr(manager, "get_engine", lambda *a, **k: None)

    await manager.deliver_to_session("gone", "hello from WhatsApp")

    items = manager.unrouted.list()
    assert len(items) == 1
    assert items[0]["text"] == "hello from WhatsApp"
    assert "resume" in items[0]["reason"]


# -- the REST hatch ------------------------------------------------------------
def test_force_idle_over_rest(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server import create_app

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    mgr = SessionManager(workspace=tmp_path)
    with TestClient(create_app(mgr)) as client:
        mgr.try_mark_running("s1")
        body = client.post("/v1/sessions/s1/force-idle").json()
        assert body == {"ok": True, "was_running": True}
        assert mgr.is_running("s1") is False


# -- how a platform message asks to be answered --------------------------------
# A channel mention always carried "you must respond… with the send_message tool". A DM
# carried only the bare `reply→` handle, and models answered in plain text — which goes
# to the app window, where the sender is not looking. The reply never arrived and
# nothing reported a failure.
def test_a_platform_message_names_the_tool_and_the_target():
    from coworker.connectors.base import MessageEvent, SessionSource

    event = MessageEvent(
        text="tudo bem?",
        source=SessionSource(
            platform="whatsapp_evolution",
            chat_id="5511999@s.whatsapp.net",
            user_name="Ana",
        ),
    )
    framed = event.tagged_text()
    assert "tudo bem?" in framed
    assert "send_message" in framed
    # The exact target, not a placeholder — the model must not have to construct it.
    assert 'target "whatsapp_evolution:5511999@s.whatsapp.net"' in framed


def test_it_says_to_answer_AFTER_the_tool_work():
    """The scenario that motivated this: "pesquise algo e me responda". Without it the
    model runs its tools and then reports to the app window, leaving the person on
    WhatsApp waiting for an answer that already exists."""
    from coworker.connectors.base import MessageEvent, SessionSource

    framed = MessageEvent(
        text="pesquise X",
        source=SessionSource(platform="slack", chat_id="C01"),
    ).tagged_text()
    assert "first" in framed.lower()
    assert "ONE send_message" in framed


def test_the_app_owner_is_still_answered_on_screen():
    """The GUI has no reply handle and needs none — framing it like a platform message
    would make the agent try to send_message to the person typing in front of it."""
    from coworker.connectors.base import MessageEvent, SessionSource

    framed = MessageEvent(
        text="oi", source=SessionSource(platform="gui", chat_id="local")
    ).tagged_text()
    assert framed == "[Owner, in the app]: oi"
    assert "send_message" not in framed


def test_the_persona_distinguishes_the_two_destinations():
    from coworker.personas.registry import PersonaRegistry

    prompt = PersonaRegistry().agent("assistant").system_prompt
    assert "do the work first" in prompt  # tools before the reply
    assert "send_message ONCE" in prompt  # one final answer, not progress notes
    assert "app itself" in prompt  # and NOT for messages typed in the app


def test_announcing_applies_to_new_threads_not_to_the_reply():
    """These two rules contradicted each other: "send ONE send_message with the finished
    answer" and "say what you are about to send before you send it". Obeying both means
    either an invisible announcement (it goes to the app window) or a second WhatsApp
    message saying a reply is coming."""
    from coworker.personas.registry import PersonaRegistry

    prompt = PersonaRegistry().agent("assistant").system_prompt
    assert "does NOT apply to answering" in prompt
    # The announce rule survives for what it was for: unprompted outbound actions.
    assert "NEW outbound thread" in prompt
