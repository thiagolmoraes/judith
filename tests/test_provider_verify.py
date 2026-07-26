"""Tests for provider key detection + the live (read-only) Test/verify path. SDK-free: the
single httpx.get is monkeypatched so no network is touched."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from coworker.providers import detect_provider, verify_provider_key


# -- detect_provider ------------------------------------------------------------
@pytest.mark.parametrize(
    "key,expected",
    [
        ("sk-ant-api03-abc", "anthropic"),
        ("AIzaSyAbc123", "gemini"),
        ("sk-proj-abc", "openai"),
        ("sk_live_abc", "openai"),
        ("", None),
        ("   ", None),
        ("nonsense", None),
    ],
)
def test_detect_provider(key, expected):
    assert detect_provider(key) == expected


# -- verify_provider_key: status-code mapping + per-provider request shape -------
def _patch_get(monkeypatch, status=200, capture=None, raise_exc=None):
    def fake_get(url, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture.update(kwargs)
        if raise_exc is not None:
            raise raise_exc
        return SimpleNamespace(status_code=status)

    monkeypatch.setattr("httpx.get", fake_get)


def test_verify_openai_ok(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    assert verify_provider_key("openai", api_key="sk-x") == {"ok": True}
    assert cap["url"] == "https://api.openai.com/v1/models"
    assert cap["headers"]["Authorization"] == "Bearer sk-x"


def test_verify_openai_custom_endpoint(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key(
        "openai", api_key="sk-x", base_url="https://gw.example/openai/v1/"
    )
    # trailing slash trimmed, /models appended to the custom endpoint
    assert cap["url"] == "https://gw.example/openai/v1/models"


def test_verify_bad_key_is_invalid(monkeypatch):
    _patch_get(monkeypatch, status=401)
    assert verify_provider_key("openai", api_key="sk-bad") == {
        "ok": False,
        "error": "Invalid API key.",
    }


def test_verify_anthropic_headers(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("anthropic", api_key="sk-ant-x")
    assert cap["url"] == "https://api.anthropic.com/v1/models"
    assert cap["headers"]["x-api-key"] == "sk-ant-x"
    assert "anthropic-version" in cap["headers"]


def test_verify_gemini_key_param(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("gemini", api_key="AIza-x")
    assert cap["params"]["key"] == "AIza-x"


def test_verify_ollama_uses_v1_models_no_key(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("ollama", base_url="http://localhost:11434")
    assert cap["url"] == "http://localhost:11434/v1/models"
    assert "headers" not in cap  # keyless


def test_verify_network_error_is_clean(monkeypatch):
    _patch_get(monkeypatch, raise_exc=ConnectionError("boom"))
    res = verify_provider_key("openai", api_key="sk-x")
    assert res["ok"] is False
    assert "Couldn't reach" in res["error"]


def test_verify_unexpected_status(monkeypatch):
    _patch_get(monkeypatch, status=500)
    res = verify_provider_key("anthropic", api_key="sk-ant-x")
    assert res["ok"] is False
    assert "500" in res["error"]


# -- keyless OpenAI-compatible endpoints (local vLLM / llama.cpp / LM Studio) -----
def test_verify_openai_compat_omits_auth_header_without_key(monkeypatch):
    """A local server that doesn't authenticate is configured with an empty key. `Bearer `
    with no value is an invalid header that httpx rejects before sending, so the header has
    to be omitted entirely rather than sent empty."""
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    res = verify_provider_key(
        "openai", api_key="", base_url="http://192.0.2.10:9001/v1"
    )
    assert res["ok"] is True
    assert cap["url"] == "http://192.0.2.10:9001/v1/models"
    assert not cap["headers"]


def test_verify_openai_sends_auth_header_when_key_present(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("openai", api_key="sk-x", base_url="http://192.0.2.10:9001/v1")
    assert cap["headers"]["Authorization"] == "Bearer sk-x"


def test_verify_local_protocol_error_does_not_blame_network(monkeypatch):
    """LocalProtocolError is raised before any bytes leave the machine, so reporting it as
    "couldn't reach" would send the user debugging their network for a request never made."""
    import httpx

    _patch_get(monkeypatch, raise_exc=httpx.LocalProtocolError("bad header"))
    res = verify_provider_key(
        "openai", api_key="", base_url="http://192.0.2.10:9001/v1"
    )
    assert res["ok"] is False
    assert "Couldn't reach" not in res["error"]
    assert "endpoint URL" in res["error"]


def test_verify_invalid_url_has_own_message(monkeypatch):
    import httpx

    _patch_get(monkeypatch, raise_exc=httpx.InvalidURL("nope"))
    res = verify_provider_key("openai", api_key="sk-x", base_url="http://:::bad")
    assert res["ok"] is False
    assert "isn't valid" in res["error"]
