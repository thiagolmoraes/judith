"""DM routing + super-agent retirement: a DM goes to the user-designated session (delivered like any
background turn) or is parked as unrouted; the legacy super-agent surface is gone."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from coworker.connectors.base import MessageEvent, SessionSource
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.providers.base import ToolCall
from coworker.server import create_app
from coworker.server.manager import SessionManager


class ScriptedProvider(ProviderClient):
    """Fails if a turn runs — several tests here assert that nothing was dispatched."""

    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


class QuietProvider(ProviderClient):
    """Answers with empty text. Spawning a DM session runs an opening turn by design,
    so tests about the SPAWN need a provider that lets it complete."""

    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="", tool_calls=[])

    def capabilities(self, model):
        return ModelCapabilities()


def _dm(text, chat_id="D1", user="bob"):
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform="slack", chat_id=chat_id, user_name=user, chat_type="dm"
        ),
    )


def _connect_slack(mgr):
    """Inbound delivery is gated on the connector being CONNECTED (§4.3). Tests used to pass
    by riding the developer's real Slack profile; with the isolated state dir (conftest) each
    test must connect its own."""
    mgr.secrets.put(
        "slack:default",
        {"bot_token": "xoxb-test", "app_token": "xapp-test", "enabled": True},
    )


def test_dm_with_designated_session_delivers(tmp_path, monkeypatch):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    _connect_slack(mgr)
    delivered: list[tuple[str, str]] = []

    async def fake_deliver(session_id, message, *, source=None):
        delivered.append((session_id, message))

    monkeypatch.setattr(mgr, "deliver_to_session", fake_deliver)
    mgr.set_dm_session("sDM")

    asyncio.run(mgr._dispatch_inbound(_dm("ping")))
    assert delivered[0][0] == "sDM"
    assert (
        "ping" in delivered[0][1]
    )  # the tagged text carries the message + a reply handle
    assert mgr.unrouted.list() == []


def test_dm_without_designation_spawns_a_session_for_that_contact(tmp_path):
    """This used to park the message. It now opens a session that belongs to the
    sender, the way a Slack mention opens one per thread.

    The parking was not just unhelpful — a single shared DM session is also open in the
    app, so "reply to this" was genuinely ambiguous and the agent answered on screen
    while the person waited on their phone. A session that exists for ONE contact has
    one way out."""
    mgr = SessionManager(workspace=tmp_path, provider=QuietProvider())
    assert mgr.dm_session() is None

    asyncio.run(mgr._dispatch_inbound(_dm("hello there")))

    assert mgr.unrouted.list() == [], "nothing should be parked any more"
    contacts = mgr.dm_sessions.all()
    assert len(contacts) == 1
    sid = contacts[0].session_id
    assert mgr.session_store.load(sid) is not None

    # The standing grant is what stops the conversation stalling on an approval the
    # sender cannot see — scoped to this contact, so any other target still asks.
    engine = mgr.get_engine(sid)
    assert contacts[0].thread_target in engine.permissions.task_rules["send_message"]


def test_a_second_message_steers_the_same_session(tmp_path):
    """One session per contact, not per message."""
    mgr = SessionManager(workspace=tmp_path, provider=QuietProvider())
    asyncio.run(mgr._dispatch_inbound(_dm("first")))
    asyncio.run(mgr._dispatch_inbound(_dm("second")))
    assert len(mgr.dm_sessions.all()) == 1


def test_a_designated_dm_session_still_wins(tmp_path):
    """Some people want one inbox for everything; setting a DM route keeps that."""
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    mgr.set_dm_session("my-inbox")
    asyncio.run(mgr._dispatch_inbound(_dm("hello there")))
    assert mgr.dm_sessions.all() == [], "no per-contact session when one is designated"


def test_dm_route_endpoints(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    client = TestClient(create_app(mgr))

    assert client.get("/v1/messaging/dm-route").json()["dm_session"] is None
    assert (
        client.post("/v1/messaging/dm-route", json={"session_id": "sX"}).json()[
            "dm_session"
        ]
        == "sX"
    )
    assert client.get("/v1/messaging/dm-route").json()["dm_session"] == "sX"
    # a falsy id clears it
    assert (
        client.post("/v1/messaging/dm-route", json={"session_id": ""}).json()[
            "dm_session"
        ]
        is None
    )


def test_dm_session_persists_across_manager_reload(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    mgr.set_dm_session("sKeep")
    # a fresh manager over the same data dir reloads the prefs-backed designation
    reborn = SessionManager(
        workspace=tmp_path, data_dir=mgr._data_base, provider=ScriptedProvider()
    )
    assert reborn.dm_session() == "sKeep"


def test_superagent_surface_is_gone(tmp_path):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    assert not hasattr(mgr, "superagent")
    assert not hasattr(mgr, "sa_register")
    client = TestClient(create_app(mgr))
    # the retired routes 404
    assert client.get("/v1/superagent").status_code == 404


# -- the answer that never left -------------------------------------------------
class _AnswersWithoutSending(ProviderClient):
    """Answers from its own knowledge — no tool call, so no send_message."""

    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(text="Minhas ferramentas são: busca, envio…", tool_calls=[])

    def capabilities(self, model):
        return ModelCapabilities()


def test_an_answer_composed_but_never_sent_is_delivered(tmp_path, monkeypatch):
    """Observed in a real WhatsApp conversation: every question that triggered a search
    or a timer was delivered, and "quais ferramentas você tem?" — answered from the
    model's own knowledge, touching no tool — was written to the app window and never
    sent. The person on WhatsApp got nothing, with no error anywhere.

    Prompt wording has not fixed this reliably and the failure is silent. The server
    knows the message came from a platform and knows the turn produced text without
    sending it, so it closes the gap."""
    sent: list[tuple[str, str]] = []

    def fake_tool(secrets, senders=None):
        def send_message(target: str, text: str):
            sent.append((target, text))
            return {"ok": True, "message_id": "M1", "target": target}

        return send_message

    import coworker.connectors.tools as tools_mod

    monkeypatch.setattr(tools_mod, "make_send_message_tool", fake_tool)

    mgr = SessionManager(workspace=tmp_path, provider=_AnswersWithoutSending())
    asyncio.run(mgr._dispatch_inbound(_dm("quais ferramentas voce tem?")))

    assert len(sent) == 1, "the composed answer should have been delivered"
    target, text = sent[0]
    assert target == "slack:D1"  # rebuilt from the message's own sidecar
    assert "ferramentas" in text


class _AnswersAndSends(ProviderClient):
    """Calls send_message itself, like a well-behaved turn."""

    def __init__(self):
        self._calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self._calls += 1
        if self._calls == 1:
            return AssistantTurn(
                text="", tool_calls=[ToolCall(id="1", name="send_message", arguments={})]
            )
        return AssistantTurn(text="done", tool_calls=[])

    def capabilities(self, model):
        return ModelCapabilities()


def test_a_turn_that_already_sent_is_not_double_delivered(tmp_path, monkeypatch):
    """The net must not turn one reply into two."""
    sent: list[tuple[str, str]] = []

    def fake_tool(secrets, senders=None):
        def send_message(target: str, text: str):
            sent.append((target, text))
            return {"ok": True, "message_id": "M", "target": target}

        return send_message

    import coworker.connectors.tools as tools_mod

    monkeypatch.setattr(tools_mod, "make_send_message_tool", fake_tool)

    mgr = SessionManager(workspace=tmp_path, provider=_AnswersAndSends())
    asyncio.run(mgr._dispatch_inbound(_dm("oi")))
    # The engine's own send_message is a different path; the net must add nothing.
    assert sent == []


# -- the echoed header ----------------------------------------------------------
def test_the_echoed_framing_header_is_stripped():
    """Models repeat the framing header at the top of their reply — it is the first
    thing they saw. Harmless in the app transcript; this text goes to a PHONE, where
    "[WhatsApp DM · Thiago | reply→whatsapp_evolution:5524…@s.whatsapp.net]" is
    unreadable noise with a raw JID in it. The user received exactly that."""
    from coworker.server.manager import _strip_reply_header

    echoed = (
        "[WhatsApp DM · Test Contact | reply→whatsapp_evolution:5511999999999"
        "@s.whatsapp.net]   Olá novamente! 😊 Como posso ajudar?"
    )
    assert _strip_reply_header(echoed) == "Olá novamente! 😊 Como posso ajudar?"


def test_stripping_leaves_an_ordinary_reply_alone():
    from coworker.server.manager import _strip_reply_header

    plain = "Olá! Encontrei três emails não lidos."
    assert _strip_reply_header(plain) == plain
    # Only a LEADING header goes: the arrow mid-sentence is someone's prose.
    mid = "Use a sintaxe reply→destino para responder."
    assert _strip_reply_header(mid) == mid


def test_a_reply_that_is_only_a_header_is_not_sent(tmp_path, monkeypatch):
    """Stripping can empty the text. Sending "" would deliver a blank WhatsApp
    message, which is worse than sending nothing — the fallback must skip the send
    entirely, not hand "" to the sender."""
    sent: list[tuple[str, str]] = []

    def fake_tool(secrets, senders=None):
        def send_message(target: str, text: str):
            sent.append((target, text))
            return {"ok": True, "message_id": "M", "target": target}

        return send_message

    import coworker.connectors.tools as tools_mod

    monkeypatch.setattr(tools_mod, "make_send_message_tool", fake_tool)

    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    asyncio.run(
        mgr._deliver_unsent_reply("s1", "slack:C1", "[WhatsApp DM · x | reply→slack:C1]")
    )
    assert sent == []  # nothing left after the strip → no send at all


def test_a_self_wake_still_knows_where_to_reply(tmp_path, monkeypatch):
    """Asked to send a message in ten minutes, the agent scheduled it correctly, woke
    on time, wrote the reply — and it went nowhere. A wake turn carries no message
    sidecar (the session is resuming ITSELF), so the reply target was empty and the
    safety net stayed out.

    A session spawned for one contact still belongs to that contact, and the durable
    map already knows it."""
    sent: list[tuple[str, str]] = []

    def fake_tool(secrets, senders=None):
        def send_message(target: str, text: str):
            sent.append((target, text))
            return {"ok": True, "message_id": "M", "target": target}

        return send_message

    import coworker.connectors.tools as tools_mod

    monkeypatch.setattr(tools_mod, "make_send_message_tool", fake_tool)

    mgr = SessionManager(workspace=tmp_path, provider=_AnswersWithoutSending())
    # First message spawns the per-contact session and its durable mapping.
    asyncio.run(mgr._dispatch_inbound(_dm("oi")))
    sent.clear()

    sid = mgr.dm_sessions.all()[0].session_id
    # Now the wake: same session, NO sidecar — exactly what _resume_wake does.
    asyncio.run(mgr.deliver_to_session(sid, "⏰ Wake — the timer you set has fired."))

    assert len(sent) == 1, "the woken turn's reply must still reach the contact"
    assert sent[0][0] == "slack:D1"


class _LeaksAToolCall(ProviderClient):
    """Writes the tool call as text the endpoint never parsed. The engine emits that
    fragment as an assistant_message, then ends the turn on the UnparsedToolCall path."""

    def complete(self, *, model, messages, tools=None, **settings):
        return AssistantTurn(
            text="Vou responder agora.\n<tool_call>\n<function=send_message>\n<parameter=target>",
            tool_calls=[],
        )

    def capabilities(self, model):
        return ModelCapabilities()


class _AnswersThenTheProviderDies(ProviderClient):
    """Round one: prose plus a tool call. Round two: the provider raises, so the engine
    ends on an error of a different type."""

    def __init__(self):
        self._calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self._calls += 1
        if self._calls == 1:
            return AssistantTurn(
                text="ok",
                tool_calls=[ToolCall(id="1", name="nope_not_a_tool", arguments={})],
            )
        raise RuntimeError("provider down")

    def capabilities(self, model):
        return ModelCapabilities()


def _spy_on_rescue(mgr, monkeypatch) -> list[str]:
    """Record every text handed to _deliver_unsent_reply, then run the real thing."""
    rescued: list[str] = []
    real = mgr._deliver_unsent_reply

    async def spy(session_id, target, text):
        rescued.append(text)
        await real(session_id, target, text)

    monkeypatch.setattr(mgr, "_deliver_unsent_reply", spy)
    return rescued


def test_an_unparsed_tool_call_fragment_is_never_delivered(tmp_path, monkeypatch):
    """The engine emits the half-written call as assistant text BEFORE it emits the
    UnparsedToolCall error. The safety net saw that text as an unsent answer and mailed
    "<function=send_message>..." to the contact's phone. A fragment is not a reply."""
    sent: list[tuple[str, str]] = []

    def fake_tool(secrets, senders=None):
        def send_message(target: str, text: str):
            sent.append((target, text))
            return {"ok": True, "message_id": "M", "target": target}

        return send_message

    import coworker.connectors.tools as tools_mod

    monkeypatch.setattr(tools_mod, "make_send_message_tool", fake_tool)

    mgr = SessionManager(workspace=tmp_path, provider=_LeaksAToolCall())
    rescued = _spy_on_rescue(mgr, monkeypatch)
    asyncio.run(mgr._dispatch_inbound(_dm("oi")))

    assert rescued == [], "the rescue must not fire on an unparsed tool call"
    assert sent == []


def test_other_errors_still_rescue_the_text_that_came_before(tmp_path, monkeypatch):
    """Only UnparsedToolCall blanks the text. An answer composed before a provider
    failure is still real and still goes out."""
    sent: list[tuple[str, str]] = []

    def fake_tool(secrets, senders=None):
        def send_message(target: str, text: str):
            sent.append((target, text))
            return {"ok": True, "message_id": "M", "target": target}

        return send_message

    import coworker.connectors.tools as tools_mod

    monkeypatch.setattr(tools_mod, "make_send_message_tool", fake_tool)

    mgr = SessionManager(workspace=tmp_path, provider=_AnswersThenTheProviderDies())
    rescued = _spy_on_rescue(mgr, monkeypatch)
    asyncio.run(mgr._dispatch_inbound(_dm("oi")))

    assert rescued == ["ok"]
    assert sent == [("slack:D1", "ok")]


def test_an_app_session_gets_no_reply_target(tmp_path):
    """A session nobody messaged from a platform must not acquire one: answering on
    screen IS the answer there, and sending would surprise whoever is typing."""
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    assert mgr._reply_target_for("some-app-session", None) == ""
    assert mgr._reply_target_for("some-app-session", {"connector": "gui"}) == ""


class _SchedulesThenTalks(ProviderClient):
    """Schedules a wake and narrates what it will do — the observed shape of "send me a
    message in 3 minutes"."""

    def __init__(self):
        self._calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self._calls += 1
        if self._calls == 1:
            return AssistantTurn(
                text="", tool_calls=[ToolCall(id="1", name="sleep_for", arguments={})]
            )
        return AssistantTurn(text="Teste de 3 minutos! 🕒", tool_calls=[])

    def capabilities(self, model):
        return ModelCapabilities()


def test_a_scheduling_turn_does_not_deliver_its_narration(tmp_path, monkeypatch):
    """Asked for a message in three minutes, the agent scheduled correctly AND wrote the
    message immediately. The safety net delivered that text one minute after the ask,
    then the woken turn delivered the real one — two messages, the first at the wrong
    time.

    Text from a turn that scheduled a wake is a plan, not an answer. The woken turn is
    the reply, and it is already covered."""
    sent: list[tuple[str, str]] = []

    def fake_tool(secrets, senders=None):
        def send_message(target: str, text: str):
            sent.append((target, text))
            return {"ok": True, "message_id": "M", "target": target}

        return send_message

    import coworker.connectors.tools as tools_mod

    monkeypatch.setattr(tools_mod, "make_send_message_tool", fake_tool)

    mgr = SessionManager(workspace=tmp_path, provider=_SchedulesThenTalks())
    asyncio.run(mgr._dispatch_inbound(_dm("me manda uma mensagem daqui 3 minutos")))

    assert sent == [], "nothing should go out until the timer fires"


# -- one rule, three paths ------------------------------------------------------
def test_an_automation_inherits_the_reply_target_of_the_chat_that_made_it(tmp_path):
    """Asked over WhatsApp for a daily good-morning, the automation runs under a FRESH
    session id — no contact mapping of its own. `origin_session_id` was recorded at
    creation and never read, so the run would fire on time and answer into a session
    nobody is watching.

    This is the third path to get the destination wrong (inbound message, self-wake,
    scheduled run), which is why it is now one lookup rather than three."""
    from coworker.automation.models import Schedule, ScheduledTask

    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    mgr.dm_sessions.set("whatsapp_evolution:5511@s.whatsapp.net", "chat-1", channel="x")

    task = ScheduledTask(
        title="Bom dia",
        instructions="mande bom dia",
        schedule=Schedule(kind="cron", cron="0 8 * * *"),
        workspace="",
        origin_session_id="chat-1",
    )
    assert mgr._origin_reply_target(task) == "whatsapp_evolution:5511@s.whatsapp.net"


def test_an_automation_made_in_the_app_has_no_platform_target(tmp_path):
    """One created from the Automations page answers in the app, as it always did."""
    from coworker.automation.models import Schedule, ScheduledTask

    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    task = ScheduledTask(
        title="x",
        instructions="y",
        schedule=Schedule(kind="cron", cron="0 8 * * *"),
        workspace="",
        origin_session_id="",
    )
    assert mgr._origin_reply_target(task) == ""


def test_the_three_paths_agree_on_the_destination(tmp_path):
    """The whole point of the single lookup: an inbound message, a wake, and an
    automation descended from the same chat must all answer the same person."""
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    target = "whatsapp_evolution:5511@s.whatsapp.net"
    mgr.dm_sessions.set(target, "chat-1", channel="x")

    from_message = mgr._reply_target_for(
        "chat-1",
        {"connector": "whatsapp_evolution", "channel_id": "5511@s.whatsapp.net"},
    )
    from_wake = mgr._reply_target_for("chat-1", None)

    from coworker.automation.models import Schedule, ScheduledTask

    from_task = mgr._origin_reply_target(
        ScheduledTask(
            title="x",
            instructions="y",
            schedule=Schedule(kind="cron", cron="0 8 * * *"),
            workspace="",
            origin_session_id="chat-1",
        )
    )
    assert from_message == from_wake == from_task == target


def test_a_mention_thread_session_resolves_its_reply_target(tmp_path):
    """A Slack mention thread's session belongs to its thread the way a DM session
    belongs to its contact — a self-wake in one must still find its way back."""
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    mgr.mention_sessions.set("slack:C9:171.42", "sess-9", channel="slack:C9")
    assert mgr._reply_target_for("sess-9", None) == "slack:C9:171.42"
