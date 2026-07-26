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


# -- key_optional: the single predicate every key gate reads ----------------------
@pytest.mark.parametrize(
    "name,base_url,expected",
    [
        # No custom endpoint => the official API => a key is always required.
        ("openai", None, False),
        ("openai", "", False),
        ("openai", "   ", False),
        ("anthropic", None, False),
        ("anthropic", "", False),
        # Stock OpenAI typed in by hand is not "custom" — a missing key must still be
        # reported as such rather than as a 401 from OpenAI. Matched on the normalized
        # hostname, so case, scheme, a default port or a trailing slash can't disguise it.
        ("openai", "https://api.openai.com/v1", False),
        ("openai", "https://api.openai.com", False),
        ("openai", "https://api.openai.com/v1/", False),
        ("openai", "https://API.OpenAI.COM/v1", False),
        ("openai", "https://Api.OpenAI.com", False),
        ("openai", "https://api.openai.com:443/v1", False),
        ("openai", "http://api.openai.com/v1", False),
        ("openai", "api.openai.com/v1", False),
        ("openai", "https://api.openai.com/V1", False),
        # A lookalike host is NOT the official one — hostname is matched exactly, not by prefix.
        ("openai", "https://api.openai.com.evil.test/v1", True),
        # A different path on the official host is a custom deployment, not the stock API.
        ("openai", "https://api.openai.com/v2", True),
        # Case-insensitive for prefilled vendor endpoints too.
        ("deepseek", "https://API.DeepSeek.com", False),
        # A prefilled vendor endpoint still needs that vendor's key.
        ("deepseek", "https://api.deepseek.com", False),
        ("deepseek", "https://api.deepseek.com/", False),
        # An endpoint the user actually redirected: may be a keyless local server.
        ("openai", "http://192.0.2.10:9001/v1", True),
        ("openai", "https://my.azure.example/openai/v1", True),
        ("deepseek", "http://192.0.2.10:9001/v1", True),
        # Keyless by nature.
        ("ollama", None, True),
        # Unknown provider: no key gate to enforce.
        ("nope", None, True),
    ],
)
def test_key_optional(name, base_url, expected):
    from coworker.providers import key_optional

    assert key_optional(name, base_url) is expected
