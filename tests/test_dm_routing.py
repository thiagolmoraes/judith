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
