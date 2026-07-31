"""GET /v1/connectors/{name}/contacts — the address-book search behind "add someone
who hasn't written in yet". The route depends on the ContactDirectory contract, so this
suite injects a fake: no Evolution, no network."""

from fastapi.testclient import TestClient

from coworker.connectors.contacts import Contact
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import create_app
from coworker.server.manager import SessionManager


class ScriptedProvider(ProviderClient):
    def complete(self, *, model, messages, tools=None, **settings):
        raise AssertionError("no turns expected")

    def capabilities(self, model):
        return ModelCapabilities()


class FakeDirectory:
    def __init__(self, contacts):
        self.contacts = contacts
        self.calls: list[tuple[str, int]] = []

    def available(self) -> bool:
        return True

    def search(self, query: str, limit: int = 20) -> list[Contact]:
        self.calls.append((query, limit))
        return self.contacts[:limit]


CONTACTS = [Contact("5511999999999", "Ana Silva"), Contact("5511888888888", None)]


def _client(tmp_path, directory, *, allowed=()):
    mgr = SessionManager(workspace=tmp_path, provider=ScriptedProvider())
    mgr.contact_directory_factory = lambda platform: directory
    mgr.allowed_users_for = lambda platform: set(allowed)
    return TestClient(create_app(mgr))


def test_search_returns_rows_with_display_and_allowed_flags(tmp_path):
    directory = FakeDirectory(CONTACTS)
    client = _client(tmp_path, directory, allowed={"5511888888888"})

    body = client.get("/v1/connectors/whatsapp_evolution/contacts?q=ana").json()

    assert body["ok"] is True
    assert body["contacts"] == [
        {
            "number": "5511999999999",
            "name": "Ana Silva",
            "display": "+55 11 99999-9999",
            "allowed": False,
        },
        {
            "number": "5511888888888",
            "name": None,
            "display": "+55 11 88888-8888",
            "allowed": True,
        },
    ]
    assert directory.calls == [("ana", 20)]


def test_a_connector_without_a_directory_says_so_instead_of_failing(tmp_path):
    client = _client(tmp_path, None)
    body = client.get("/v1/connectors/telegram/contacts").json()
    assert body["ok"] is False
    assert body["contacts"] == []


def test_limit_is_clamped_to_a_sane_range(tmp_path):
    directory = FakeDirectory(CONTACTS)
    client = _client(tmp_path, directory)

    client.get("/v1/connectors/whatsapp_evolution/contacts?limit=1000")
    client.get("/v1/connectors/whatsapp_evolution/contacts?limit=0")

    assert [limit for _q, limit in directory.calls] == [100, 1]
