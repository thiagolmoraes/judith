"""DM routing + super-agent retirement: a DM goes to the user-designated session (delivered like any
background turn) or is parked as unrouted; the legacy super-agent surface is gone."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from coworker.connectors.base import MessageEvent, SessionSource
from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
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
        "[WhatsApp DM · Thiago Mangia | reply→whatsapp_evolution:5524998797932"
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
    message, which is worse than sending nothing."""
    from coworker.server.manager import _strip_reply_header

    assert _strip_reply_header("[WhatsApp DM · x | reply→slack:C1]") == ""
