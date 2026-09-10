"""The running flag, and what happens to messages that arrive while it is set.

Found the hard way: a WhatsApp DM reached the sidecar, the webhook answered
`{"ok": true, "handled": true}`, and nothing happened — repeatedly, for half an hour,
while I blamed the network. The session's running flag had been left set by a turn whose
cleanup never ran, so every later message was queued into a turn that would never
execute. A stuck session is indistinguishable from a busy one, the queue is invisible,
and nothing in the app can clear it.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager


@pytest.fixture
def make_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)

    def build(**kwargs):
        return SessionManager(workspace=tmp_path, **kwargs)

    return build


@pytest.fixture
def manager(make_manager):
    return make_manager()


class _BlocksUntilReleased(ProviderClient):
    """Holds the turn open until the test says go. The engine calls the provider on a
    worker thread, so blocking here leaves the event loop free."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, *, model, messages, tools=None, **settings):
        self.started.set()
        assert self.release.wait(timeout=10), "test never released the provider"
        return AssistantTurn(text="hi", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


class _RecordingProvider(ProviderClient):
    """Answers plain text, in order, and keeps every message list it was handed."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls.append([dict(m) for m in messages])
        return AssistantTurn(text=self.replies.pop(0), finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


async def _wait_until(condition, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition never held"
        await asyncio.sleep(0.01)


def _user_texts(call: list[dict]) -> list[str]:
    return [str(m["content"]) for m in call if m.get("role") == "user"]


def _first_seen_order(calls: list[list[dict]], texts: tuple[str, ...]) -> list[str]:
    """The order in which the model first saw each of `texts`, across every call."""
    seen: list[str] = []
    for call in calls:
        for user_text in _user_texts(call):
            for wanted in texts:
                if wanted in user_text and wanted not in seen:
                    seen.append(wanted)
    return seen


def _viewer_log(manager, session_id: str) -> list[dict]:
    seen: list[dict] = []

    async def viewer(message):
        seen.append(message)

    manager.register_session_client(session_id, viewer)
    return seen


# -- the flag ------------------------------------------------------------------
def test_the_flag_gates_a_second_turn(manager):
    assert manager.try_mark_running("s1") is True
    assert manager.try_mark_running("s1") is False  # already running
    manager.mark_idle("s1")
    assert manager.try_mark_running("s1") is True


async def test_force_idle_clears_a_stuck_flag(manager):
    """The escape hatch. The flag is in-memory and per-process, so before this there was
    no way — API, GUI or otherwise — to clear one that got stuck."""
    manager.try_mark_running("s1")
    assert manager.is_running("s1") is True

    result = await manager.force_idle("s1")
    assert result["ok"] is True
    assert result["was_running"] is True
    assert manager.is_running("s1") is False
    # And the session accepts turns again.
    assert manager.try_mark_running("s1") is True


async def test_force_idle_on_an_idle_session_is_a_no_op(manager):
    """Reports what it found rather than pretending: `was_running` is how a caller
    learns whether it actually unstuck anything."""
    assert await manager.force_idle("never-ran") == {"ok": True, "was_running": False, "queued": 0}


@pytest.mark.parametrize(
    ("mark_running", "was_running"),
    [
        pytest.param(True, True, id="stuck-flag"),
        pytest.param(False, False, id="nothing-stuck"),
    ],
)
async def test_force_idle_tells_every_viewer_the_turn_is_over(
    manager, mark_running, was_running
):
    """A GUI socket watching a stuck session shows Stop and a waiting row until it hears
    turn_done. Clearing the flag alone leaves that screen frozen. Sent even when the
    flag was already clear: the GUI may still think it is running, and the App handler
    only flips running off, so the reset is cheap and safe."""
    seen = _viewer_log(manager, "s1")
    if mark_running:
        manager.try_mark_running("s1")

    result = await manager.force_idle("s1")

    assert result == {"ok": True, "was_running": was_running, "queued": 0}
    assert seen == [{"type": "turn_done", "data": {}}]


async def test_force_idle_survives_a_dead_viewer(manager):
    """broadcast_session drops a socket that raises. The flag must still clear and the
    call must still answer."""

    async def dead(message):
        raise RuntimeError("socket closed")

    manager.register_session_client("s1", dead)
    manager.try_mark_running("s1")

    result = await manager.force_idle("s1")

    assert result == {"ok": True, "was_running": True, "queued": 0}
    assert manager.is_running("s1") is False
    assert manager.has_session_clients("s1") is False


# -- a live turn is not a stuck flag ---------------------------------------------
# The flag cannot tell the two apart. The turn task can: a turn that died before its
# cleanup left a done (or never bound) task, a live one is still pending.
async def test_force_idle_refuses_while_the_turn_task_is_alive(manager):
    """Releasing under a live turn would let the next message start a second engine
    run on top of it, and the turn_done would make the GUI drop Stop mid-turn."""
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    seen = _viewer_log(manager, "s1")
    manager.try_mark_running("s1")
    manager.bind_turn_task("s1", task)
    try:
        assert manager.turn_alive("s1") is True

        result = await manager.force_idle("s1")

        assert result == {"ok": False, "reason": "turn_alive", "was_running": True, "queued": 0}
        assert manager.is_running("s1") is True
        assert seen == []
    finally:
        gate.set()
        await task


async def test_force_idle_releases_once_the_turn_task_has_finished(manager):
    """A finished task with the flag still set is exactly the stuck case."""
    task = asyncio.create_task(asyncio.sleep(0))
    manager.try_mark_running("s1")
    manager.bind_turn_task("s1", task)
    await task
    assert manager.turn_alive("s1") is False

    result = await manager.force_idle("s1")

    assert result == {"ok": True, "was_running": True, "queued": 0}
    assert manager.is_running("s1") is False


async def test_force_idle_with_force_overrides_a_live_turn(manager):
    """The explicit override. The caller takes the two-runs risk on purpose."""
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    seen = _viewer_log(manager, "s1")
    manager.try_mark_running("s1")
    manager.bind_turn_task("s1", task)
    try:
        result = await manager.force_idle("s1", force=True)

        assert result == {"ok": True, "was_running": True, "queued": 0}
        assert manager.is_running("s1") is False
        assert seen == [{"type": "turn_done", "data": {}}]
    finally:
        gate.set()
        await task


async def test_a_turn_ends_its_own_binding_when_it_marks_idle(manager):
    """Both turn paths call mark_idle from inside the turn task, then await the
    turn_done broadcast. From mark_idle on the turn is over, even though the task
    is still pending on that last await."""
    gate = asyncio.Event()

    async def turn():
        manager.try_mark_running("s1")
        manager.bind_turn_task("s1", asyncio.current_task())
        manager.mark_idle("s1")
        await gate.wait()

    task = asyncio.create_task(turn())
    await asyncio.sleep(0)
    try:
        assert task.done() is False
        assert manager.turn_alive("s1") is False
    finally:
        gate.set()
        await task


async def test_a_late_mark_idle_keeps_a_newer_turns_binding(manager):
    """The WS disconnect backstop calls mark_idle from a done-callback, possibly
    after another driver claimed the session. That live turn must not look dead."""
    gate = asyncio.Event()
    newer = asyncio.create_task(gate.wait())
    manager.try_mark_running("s1")
    manager.bind_turn_task("s1", newer)
    try:
        manager.mark_idle("s1")  # from a task that is not the bound one

        assert manager.turn_alive("s1") is True
    finally:
        gate.set()
        await newer


async def test_a_background_turn_counts_as_alive_until_it_ends(make_manager):
    """deliver_to_session binds itself after claiming the flag. While its provider
    call is open the release is refused. Once it ends, the binding is gone."""
    provider = _BlocksUntilReleased()
    manager = make_manager(provider=provider)
    turn = asyncio.create_task(manager.deliver_to_session("s1", "hello"))
    await asyncio.to_thread(provider.started.wait, 10)
    try:
        assert manager.turn_alive("s1") is True
        refused = await manager.force_idle("s1")
        assert refused["ok"] is False and refused["reason"] == "turn_alive"
        assert manager.is_running("s1") is True
    finally:
        provider.release.set()
        await turn
    assert manager.turn_alive("s1") is False
    assert manager.is_running("s1") is False


# -- the queue a stuck session left behind --------------------------------------
async def test_force_idle_hands_the_backlog_to_a_fresh_turn(make_manager, monkeypatch):
    """Messages that arrived while the flag was stuck went to the engine's steering
    queue, which only empties on the next run. Clearing the flag delivered nothing.
    The release now opens that run with the oldest message; the engine injects the
    rest at its first step, so one turn answers all of them."""
    provider = _RecordingProvider(["first reply", "second reply"])
    manager = make_manager(provider=provider)
    # Auto-title rides mark_idle and would make a third provider call. Not under test.
    monkeypatch.setattr(manager, "_maybe_autotitle", lambda session_id: None)
    engine = manager.get_engine("s1")
    manager.try_mark_running("s1")  # stuck: the flag is set, no turn task is bound
    await manager.deliver_to_session("s1", "first while stuck")
    await manager.deliver_to_session("s1", "second while stuck")
    assert engine.steering_backlog() == 2

    result = await manager.force_idle("s1")

    assert result == {"ok": True, "was_running": True, "queued": 1}
    await _wait_until(lambda: len(provider.calls) == 2 and not manager.is_running("s1"))
    first, second = provider.calls
    assert "first while stuck" in _user_texts(first)[-1]
    assert not any("second while stuck" in t for t in _user_texts(first))
    assert "first while stuck" in _user_texts(second)[-2]
    assert "second while stuck" in _user_texts(second)[-1]
    assert engine.steering_backlog() == 0


async def test_an_inbound_in_the_turn_done_gap_queues_behind_the_backlog(
    make_manager, monkeypatch
):
    """force_idle clears the flag, then awaits the turn_done broadcast. That await
    yields the loop. An inbound landing right there passed try_mark_running and
    opened its own run. The drain that followed found the session busy and queued
    the oldest message BEHIND the two that arrived after it: the model saw them as
    [rival, second, third, first]. The drain now claims the flag before the
    broadcast, so the inbound is the one that queues, at the end."""
    provider = _RecordingProvider(["reply"] * 4)
    manager = make_manager(provider=provider)
    monkeypatch.setattr(manager, "_maybe_autotitle", lambda session_id: None)
    manager.get_engine("s1")
    manager.try_mark_running("s1")  # stuck: the flag is set, no turn task is bound
    for text in ("first", "second", "third"):
        await manager.deliver_to_session("s1", text)
    rival: list[asyncio.Task] = []
    real_broadcast = manager.broadcast_session

    async def inbound_in_the_gap(session_id, message):
        if message.get("type") == "turn_done" and not rival:
            # The path a WhatsApp DM takes. Not awaited: like a real inbound it runs
            # when the loop next yields, which this broadcast is about to do.
            rival.append(
                asyncio.create_task(manager.deliver_to_session(session_id, "rival"))
            )
        await real_broadcast(session_id, message)

    monkeypatch.setattr(manager, "broadcast_session", inbound_in_the_gap)

    result = await manager.force_idle("s1")

    assert result == {"ok": True, "was_running": True, "queued": 2}
    await _wait_until(
        lambda: bool(rival)
        and rival[0].done()
        and len(provider.calls) >= 2
        and not manager.is_running("s1")
    )
    await asyncio.gather(*rival)
    assert _first_seen_order(provider.calls, ("first", "second", "third", "rival")) == [
        "first",
        "second",
        "third",
        "rival",
    ]


async def test_the_release_owns_the_next_turn_before_it_says_turn_done(
    make_manager, monkeypatch
):
    """What anything that runs during the turn_done broadcast finds: the session
    already claimed for its drain turn, with a live task bound to it."""
    provider = _RecordingProvider(["reply"])
    manager = make_manager(provider=provider)
    monkeypatch.setattr(manager, "_maybe_autotitle", lambda session_id: None)
    manager.get_engine("s1")
    manager.try_mark_running("s1")
    await manager.deliver_to_session("s1", "while stuck")
    at_turn_done: list[tuple[bool, bool]] = []
    real_broadcast = manager.broadcast_session

    async def peek(session_id, message):
        if message.get("type") == "turn_done" and not at_turn_done:
            at_turn_done.append(
                (manager.is_running(session_id), manager.turn_alive(session_id))
            )
        await real_broadcast(session_id, message)

    monkeypatch.setattr(manager, "broadcast_session", peek)

    await manager.force_idle("s1")

    assert at_turn_done == [(True, True)]
    await _wait_until(lambda: not manager.is_running("s1"))


async def test_a_release_that_cannot_claim_puts_the_message_back_in_front(
    manager, monkeypatch, caplog
):
    """Not expected: the flag was cleared a moment before and nothing awaited since.
    If it happens anyway, the popped message goes back to the HEAD of the queue.
    Appending it would send it after everything that arrived later."""
    engine = manager.get_engine("s1")
    manager.try_mark_running("s1")
    await manager.deliver_to_session("s1", "first")
    await manager.deliver_to_session("s1", "second")
    monkeypatch.setattr(manager, "try_mark_running", lambda session_id: False)

    with caplog.at_level("WARNING"):
        result = await manager.force_idle("s1")

    assert result == {"ok": True, "was_running": True, "queued": 2}
    assert engine.pop_steering() == ("first", None)
    assert engine.pop_steering() == ("second", None)
    assert "could not claim s1" in caplog.text


async def test_a_drain_turn_that_crashes_is_logged_parked_and_unstuck(
    manager, monkeypatch, caplog
):
    """deliver_to_session catches what its run raises. Anything that gets past it
    ended in a task nobody awaited, and the message with it. The done callback
    logs it, parks the message where a person can find it, and gives the flag
    back, since the turn died before its own cleanup could."""
    manager.get_engine("s1")
    manager.try_mark_running("s1")
    await manager.deliver_to_session("s1", "hello from WhatsApp")

    async def crash(session_id, message, *, source=None, claimed=False):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(manager, "deliver_to_session", crash)

    with caplog.at_level("WARNING"):
        result = await manager.force_idle("s1")
        await _wait_until(lambda: bool(manager.unrouted.list()))

    assert result == {"ok": True, "was_running": True, "queued": 0}
    assert "drain turn crashed for s1: engine exploded" in caplog.text
    items = manager.unrouted.list()
    assert len(items) == 1
    assert items[0]["text"] == "hello from WhatsApp"
    assert items[0]["reason"] == "engine exploded"
    assert manager.is_running("s1") is False
    assert manager.turn_alive("s1") is False


async def test_a_cancelled_drain_turn_is_not_a_crash(make_manager, monkeypatch, caplog):
    """Cancellation is how a shutdown ends a turn. Nothing to log, nothing to park."""
    provider = _BlocksUntilReleased()
    manager = make_manager(provider=provider)
    monkeypatch.setattr(manager, "_maybe_autotitle", lambda session_id: None)
    manager.get_engine("s1")
    manager.try_mark_running("s1")
    await manager.deliver_to_session("s1", "while stuck")

    with caplog.at_level("WARNING"):
        await manager.force_idle("s1")
        await asyncio.to_thread(provider.started.wait, 10)
        (turn,) = manager._drain_tasks
        turn.cancel()
        try:
            await turn
        except asyncio.CancelledError:
            pass
        finally:
            provider.release.set()

    assert caplog.text == ""
    assert manager.unrouted.list() == []
    assert manager.is_running("s1") is False


async def test_a_forced_release_under_a_live_turn_leaves_the_queue_to_it(manager):
    """That turn injects its own queue at its next step. Draining here would start a
    second run that steals the oldest message from it."""
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    queued: list[str] = []

    class _Engine:
        messages: list[dict] = []  # mark_idle's auto-title hook reads this

        def queue_steering(self, message, source=None):
            queued.append(message)

        def steering_backlog(self):
            return len(queued)

        def pop_steering(self):  # pragma: no cover - must not be called
            raise AssertionError("a live turn's queue was drained")

    manager._engines["s1"] = _Engine()
    manager.try_mark_running("s1")
    manager.bind_turn_task("s1", task)
    await manager.deliver_to_session("s1", "arrived mid-turn")
    try:
        refused = await manager.force_idle("s1")
        assert refused == {
            "ok": False,
            "reason": "turn_alive",
            "was_running": True,
            "queued": 1,
        }

        forced = await manager.force_idle("s1", force=True)

        assert forced == {"ok": True, "was_running": True, "queued": 1}
        assert queued == ["arrived mid-turn"]
    finally:
        gate.set()
        await task


@pytest.mark.parametrize("claimed", [False, True], ids=["claims-itself", "claimed-by-release"])
async def test_a_turn_that_fails_before_it_runs_still_frees_the_session(
    manager, monkeypatch, claimed
):
    """bind_turn_task and _reply_target_for sat between the claim and the try whose
    finally gives the flag back. A raise there left the flag set, with a binding
    to a task already finished: a stuck session made by the code meant to run
    the turn. Both now sit inside the try."""
    manager.get_engine("s1")

    def no_target(session_id, source=None):
        raise RuntimeError("no target")

    monkeypatch.setattr(manager, "_reply_target_for", no_target)
    if claimed:
        assert manager.try_mark_running("s1") is True

    try:
        await manager.deliver_to_session("s1", "hello", claimed=claimed)
    except RuntimeError:
        pass  # the old code let it out; the session must be free either way

    assert manager.is_running("s1") is False
    assert manager.turn_alive("s1") is False
    items = manager.unrouted.list()
    assert len(items) == 1
    assert items[0]["text"] == "hello"
    assert items[0]["reason"] == "no target"


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


# The REST hatch itself is covered in tests/test_server.py, next to the other routes.


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
