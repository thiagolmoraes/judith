"""Persistent approvals over the server API: GET /v1/approvals snapshots the store,
POST /v1/approvals/revoke removes one grant (tool / command / target) — what the
Settings screen drives."""

from fastapi.testclient import TestClient

from coworker.approval_store import ApprovalStore
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import create_app
from coworker.server.manager import SessionManager


class ScriptedProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


def _client(tmp_path, monkeypatch):
    # The route resolves the store from state_dir(); pin it to the test sandbox.
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    return TestClient(create_app(mgr)), ApprovalStore(
        tmp_path / "state" / "approvals.json"
    )


def test_snapshot_reflects_grants(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    assert client.get("/v1/approvals").json() == {
        "allow_tools": [],
        "allow_commands": [],
        "allow_targets": {},
    }

    store.grant_tool("delete_scheduled_task")
    store.grant_target("send_message", "whatsapp_evolution:5511999999999")
    got = client.get("/v1/approvals").json()
    assert got["allow_tools"] == ["delete_scheduled_task"]
    assert got["allow_targets"] == {
        "send_message": ["whatsapp_evolution:5511999999999"]
    }


def test_revoke_each_kind(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    store.grant_tool("delete_scheduled_task")
    store.grant_command("git status")
    store.grant_target("send_message", "whatsapp_evolution:5511999999999")

    r = client.post(
        "/v1/approvals/revoke", json={"kind": "tool", "value": "delete_scheduled_task"}
    )
    assert r.json()["ok"] is True
    assert r.json()["allow_tools"] == []

    client.post("/v1/approvals/revoke", json={"kind": "command", "value": "git status"})
    r = client.post(
        "/v1/approvals/revoke",
        json={
            "kind": "target",
            "tool": "send_message",
            "value": "whatsapp_evolution:5511999999999",
        },
    )
    body = r.json()
    assert body["ok"] is True
    assert body["allow_commands"] == []
    assert body["allow_targets"] == {}


def test_revoke_rejects_unknown_kind(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    assert client.post("/v1/approvals/revoke", json={"kind": "nope"}).json() == {
        "ok": False,
        "error": "unknown kind",
    }
