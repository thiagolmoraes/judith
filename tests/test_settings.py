"""Tests for the model API-key settings path (Tauri desktop Phase 2).

A Tauri-launched sidecar doesn't inherit the shell env, so the key may live only in the
SecretStore. These cover: the env→store resolver, the status shape (never leaks the key),
and the REST round-trip. No network, no model calls.
"""

from __future__ import annotations

from pathlib import Path

from coworker.providers import resolve_api_key
from coworker.secrets import SecretStore


def test_resolve_api_key_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-123")
    secrets = SecretStore(path=tmp_path / "secrets.json")
    secrets.put("provider:openai", {"type": "api_key", "api_key": "sk-store-999"})
    assert resolve_api_key(secrets) == "sk-env-123"


def test_resolve_api_key_falls_back_to_store(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    secrets = SecretStore(path=tmp_path / "secrets.json")
    assert resolve_api_key(secrets) is None
    secrets.put("provider:openai", {"type": "api_key", "api_key": "sk-store-999"})
    assert resolve_api_key(secrets) == "sk-store-999"


def test_settings_rest_roundtrip(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    client = TestClient(create_app(manager))

    before = client.get("/v1/settings").json()
    assert (
        before["has_key"] is False
        and before["source"] is None
        and before["provider"] == "openai"
    )
    assert before["onboarded"] is False and before["model"] in before["models"]

    set_resp = client.post(
        "/v1/settings/model-key", json={"api_key": "sk-secret-xyz"}
    ).json()
    assert (
        set_resp["ok"] is True
        and set_resp["has_key"] is True
        and set_resp["source"] == "store"
    )

    after = client.get("/v1/settings").json()
    assert after["has_key"] is True
    # the key value is never returned by either endpoint
    assert "sk-secret-xyz" not in str(set_resp) and "api_key" not in after

    # empty key is rejected
    assert (
        client.post("/v1/settings/model-key", json={"api_key": "  "}).json()["ok"]
        is False
    )


def test_default_model_and_onboarding_persist(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    client = TestClient(create_app(SessionManager(data_dir=data_dir)))

    # set a default model + mark onboarded
    assert (
        client.post("/v1/settings/default-model", json={"model": "gpt-4o"}).json()[
            "model"
        ]
        == "gpt-4o"
    )
    assert (
        client.post("/v1/settings/onboarded", json={"value": True}).json()["onboarded"]
        is True
    )
    assert (
        client.post("/v1/settings/default-model", json={"model": " "}).json()["ok"]
        is False
    )

    # a fresh manager over the same data dir restores both from prefs.json
    reborn = SessionManager(data_dir=data_dir)
    assert reborn.model == "gpt-4o"
    s = reborn.get_settings()
    assert s["onboarded"] is True and s["model"] == "gpt-4o"


def test_nav_layout_setting_roundtrips(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    client = TestClient(create_app(SessionManager(data_dir=data_dir)))

    # defaults to "flat"
    assert client.get("/v1/settings").json()["nav_layout"] == "flat"

    resp = client.post("/v1/settings/nav-layout", json={"nav_layout": "grouped"}).json()
    assert resp == {"ok": True, "nav_layout": "grouped"}
    assert client.get("/v1/settings").json()["nav_layout"] == "grouped"

    # unknown value falls back to flat; persists across a restart
    assert (
        client.post("/v1/settings/nav-layout", json={"nav_layout": "bogus"}).json()[
            "nav_layout"
        ]
        == "flat"
    )
    client.post("/v1/settings/nav-layout", json={"nav_layout": "grouped"})
    reborn = SessionManager(data_dir=data_dir)
    assert reborn.get_settings()["nav_layout"] == "grouped"


def test_scratch_base_setting_persists_and_drives_provisioning(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    data_dir = tmp_path / "data"
    client = TestClient(create_app(SessionManager(data_dir=data_dir)))

    # defaults to ~/OpenWorker
    assert client.get("/v1/settings").json()["scratch_base"] == "~/OpenWorker"

    base = tmp_path / "my coworker files"
    resp = client.post("/v1/settings/scratch-base", json={"path": str(base)}).json()
    assert resp["ok"] is True and resp["scratch_base"] == str(base)
    assert base.is_dir()  # created on set
    assert (
        client.post("/v1/settings/scratch-base", json={"path": " "}).json()["ok"]
        is False
    )

    # persists across a restart and actually drives where scratch dirs are provisioned
    reborn = SessionManager(data_dir=data_dir)
    assert reborn.get_settings()["scratch_base"] == str(base)
    scratch = reborn._provision_scratch("sess-xyz")
    assert Path(scratch) == (base / "sess-xyz").resolve() and Path(scratch).is_dir()


def test_ollama_models_gated_on_liveness(tmp_path, monkeypatch):
    """`ollama:*` entries show only while a local Ollama answers — keyless must not mean
    always-present (a stray ollama:<junk> pref would otherwise render forever)."""
    from coworker.server.manager import SessionManager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.add_model("ollama:llama3.3")

    monkeypatch.setattr(SessionManager, "_ollama_alive", lambda self: False)
    assert "ollama:llama3.3" not in manager.get_settings()["models"]

    monkeypatch.setattr(SessionManager, "_ollama_alive", lambda self: True)
    assert "ollama:llama3.3" in manager.get_settings()["models"]


def test_local_server_liveness_accepts_openai_compatible(tmp_path, monkeypatch):
    """The liveness probe backing `ollama:*` must accept an OpenAI-compatible local server
    (vLLM, llama.cpp, LM Studio), not just Ollama's native API. Those serve `/v1/models` but
    404 on `/api/tags`, which alone reads as "nothing running" and culls their models.
    """
    from types import SimpleNamespace

    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("provider:ollama", {"base_url": "http://192.0.2.10:9001/v1"})

    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        # vLLM: no native Ollama API, but a working OpenAI-compatible surface.
        code = 404 if url.endswith("/api/tags") else 200
        return SimpleNamespace(
            status_code=code, json=lambda: {"data": [{"id": "qwen3-14b"}]}
        )

    monkeypatch.setattr("httpx.get", fake_get)
    manager._ollama_alive_cache = None

    assert manager._ollama_alive() is True
    assert seen == [
        "http://192.0.2.10:9001/api/tags",  # native tried first
        "http://192.0.2.10:9001/v1/models",  # then the compat fallback
    ]
    # ...and the compat listing is read from `/v1/models`'s `data[].id`.
    assert manager._ollama_models() == ["ollama:qwen3-14b"]


def test_local_server_liveness_false_when_nothing_answers(tmp_path, monkeypatch):
    """Both probes failing still means "not present" — the anti-phantom guarantee holds."""
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("provider:ollama", {"base_url": "http://192.0.2.10:9001/v1"})

    def fake_get(url, **kwargs):
        raise ConnectionError("refused")

    monkeypatch.setattr("httpx.get", fake_get)
    manager._ollama_alive_cache = None

    assert manager._ollama_alive() is False
    assert manager._ollama_models() == []


def test_native_ollama_listing_still_uses_api_tags(tmp_path, monkeypatch):
    """A real Ollama must keep being read via `/api/tags` (`models[].name`) — the fallback is
    additive and must not change behavior where the native API answers."""
    from types import SimpleNamespace

    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("provider:ollama", {"base_url": "http://localhost:11434"})

    def fake_get(url, **kwargs):
        assert url.endswith("/api/tags"), f"compat path should not be probed: {url}"
        return SimpleNamespace(
            status_code=200, json=lambda: {"models": [{"name": "qwen3-coder:30b"}]}
        )

    monkeypatch.setattr("httpx.get", fake_get)
    manager._ollama_alive_cache = None

    assert manager._ollama_alive() is True
    assert manager._ollama_models() == ["ollama:qwen3-coder:30b"]


def test_keyless_custom_endpoint_is_testable_and_ready(tmp_path, monkeypatch):
    """A custom endpoint with no key must be usable end to end through the GUI path: the Test
    button probes it, the provider counts as configured, and its models stay in the picker.
    Building the client alone isn't enough — `verify_provider` and `_provider_configured` are
    separate gates that would otherwise reject the same setup.
    """
    from types import SimpleNamespace

    from coworker.server.manager import SessionManager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        "httpx.get", lambda url, **kw: SimpleNamespace(status_code=200, json=lambda: {})
    )

    manager = SessionManager(data_dir=tmp_path / "data")
    manager.secrets.put("provider:openai", {"base_url": "http://192.0.2.10:9001/v1"})

    assert manager.verify_provider("openai", {})["ok"] is True
    assert manager._provider_configured("openai") is True

    manager.add_model("qwen3-14b")
    settings = manager.get_settings()
    assert "qwen3-14b" in settings["models"]

    # The Settings pane reads get_providers(), which must agree with the picker rather than
    # calling this provider unconfigured while its models are selectable.
    provs = {p["name"]: p for p in manager.get_providers()}
    assert provs["openai"]["configured"] is True


def test_keyless_custom_endpoint_can_be_saved(tmp_path, monkeypatch):
    """`set_provider` treats `api_key` as required, so an endpoint-only save was rejected with
    "missing: OpenAI API key" — the provider could never be configured at all, no matter what
    the other gates allowed. A user-supplied endpoint exempts the key."""
    from coworker.server.manager import SessionManager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")

    res = manager.set_provider("openai", {"base_url": "http://192.0.2.10:9001/v1"})
    assert res["ok"] is True, res
    stored = manager.secrets.get("provider:openai") or {}
    assert stored["base_url"] == "http://192.0.2.10:9001/v1"
    assert not stored.get("api_key")

    # ...but with no endpoint at all (a separate, empty state dir) the key is still required.
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state2"))
    bare = SessionManager(data_dir=tmp_path / "data2")
    res = bare.set_provider("openai", {})
    assert res["ok"] is False
    assert "missing" in res["error"]


def test_official_endpoint_still_requires_a_key(tmp_path, monkeypatch):
    """The keyless path must not weaken the stock OpenAI gate — no key means not testable and
    not configured, so the GUI keeps telling the user to add one."""
    from coworker.server.manager import SessionManager

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")

    res = manager.verify_provider("openai", {})
    assert res["ok"] is False
    assert "Enter an API key" in res["error"]
    assert manager._provider_configured("openai") is False
    assert {p["name"]: p for p in manager.get_providers()}["openai"][
        "configured"
    ] is False

    # Typing the official URL into the custom-endpoint box must not bypass the gate either.
    manager.secrets.put("provider:openai", {"base_url": "https://api.openai.com/v1"})
    assert manager.verify_provider("openai", {})["ok"] is False
    assert manager._provider_configured("openai") is False
