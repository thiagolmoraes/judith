"""Bring-your-own OAuth: consent URL, code exchange, refresh, and the GitHub App JWT path.

Network-free — the single `httpx` call in each path is monkeypatched, like
`test_provider_verify.py` does for the provider probe.
"""

from __future__ import annotations

import time
import urllib.parse as urlparse
from types import SimpleNamespace

import pytest

from coworker.connectors import byo_github as G
from coworker.connectors import byo_oauth as B
from coworker.secrets import SecretStore

REDIRECT = "http://127.0.0.1:8765/oauth/callback"


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    B._pending.clear()
    G._token_cache.clear()
    return SecretStore(tmp_path / "secrets.json")


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in urlparse.parse_qs(urlparse.urlsplit(url).query).items()}


# -- config ---------------------------------------------------------------------
def test_byo_unconfigured_is_unavailable(secrets):
    assert B.byo_available(secrets, "notion") is False
    # An unknown connector never claims a BYO path, so the GUI can't offer one.
    assert B.byo_available(secrets, "datadog") is False


def test_set_byo_config_roundtrip_and_clear(secrets):
    assert B.set_byo_config(
        secrets, "notion", {"client_id": "cid", "client_secret": "sec"}
    )["ok"]
    assert B.byo_available(secrets, "notion") is True
    # Blank client_id clears the entry — how the GUI turns BYO back off.
    assert B.set_byo_config(secrets, "notion", {"client_id": ""}) == {
        "ok": True,
        "configured": False,
    }
    assert B.byo_available(secrets, "notion") is False


def test_blank_secret_on_resubmit_keeps_stored_one(secrets):
    """The GUI masks a stored secret, so a re-submit arrives blank; treating that as
    "overwrite with empty" would break a working connection."""
    B.set_byo_config(secrets, "notion", {"client_id": "cid", "client_secret": "sec"})
    assert B.set_byo_config(secrets, "notion", {"client_id": "cid2"})["ok"] is True
    assert B.byo_config(secrets, "notion")["client_secret"] == "sec"
    assert B.byo_config(secrets, "notion")["client_id"] == "cid2"


def test_secret_required_on_first_save(secrets):
    res = B.set_byo_config(secrets, "notion", {"client_id": "cid"})
    assert res["ok"] is False and "client_secret" in res["error"]


def test_scopes_accept_string_or_list(secrets):
    B.set_byo_config(
        secrets, "gmail", {"client_id": "c", "client_secret": "s", "scopes": "a, b  c"}
    )
    assert B.byo_config(secrets, "gmail")["scopes"] == ["a", "b", "c"]


# -- consent URL ----------------------------------------------------------------
def test_begin_requires_configured_app(secrets):
    res = B.begin_byo_connect(secrets, "notion", redirect=REDIRECT)
    assert res["ok"] is False and "no BYO app" in res["error"]


def test_begin_rejects_unknown_connector(secrets):
    assert B.begin_byo_connect(secrets, "datadog", redirect=REDIRECT)["ok"] is False


def test_google_consent_url_requests_offline_access_and_pkce(secrets):
    """Without access_type=offline + prompt=consent Google issues no refresh_token, and the
    connection would die at the first expiry."""
    B.set_byo_config(secrets, "gmail", {"client_id": "gid", "client_secret": "gsec"})
    res = B.begin_byo_connect(secrets, "gmail", redirect=REDIRECT)
    assert res["ok"] is True
    q = _query(res["authorize_url"])
    assert q["access_type"] == "offline"
    assert q["prompt"] == "consent"
    assert q["response_type"] == "code"
    assert q["redirect_uri"] == REDIRECT
    assert q["code_challenge_method"] == "S256"
    assert len(q["code_challenge"]) == 43  # base64url of a SHA-256 digest, unpadded
    assert q["scope"] == "https://www.googleapis.com/auth/gmail.modify"


def test_notion_consent_url_omits_pkce(secrets):
    """Notion rejects the PKCE params, so they must not be sent."""
    B.set_byo_config(secrets, "notion", {"client_id": "cid", "client_secret": "sec"})
    q = _query(
        B.begin_byo_connect(secrets, "notion", redirect=REDIRECT)["authorize_url"]
    )
    assert "code_challenge" not in q
    assert q["owner"] == "user"


def test_state_is_single_use(secrets):
    B.set_byo_config(secrets, "notion", {"client_id": "cid", "client_secret": "sec"})
    state = B.begin_byo_connect(secrets, "notion", redirect=REDIRECT)["state"]
    assert B.consume_byo_state(state) is not None
    assert B.consume_byo_state(state) is None


def test_expired_state_is_rejected(secrets, monkeypatch):
    B.set_byo_config(secrets, "notion", {"client_id": "cid", "client_secret": "sec"})
    state = B.begin_byo_connect(secrets, "notion", redirect=REDIRECT)["state"]
    monkeypatch.setattr(B, "_now", lambda: time.time() + B._PENDING_TTL + 10)
    assert B.consume_byo_state(state) is None


# -- code exchange --------------------------------------------------------------
def _patch_token(monkeypatch, body, capture=None, status=200):
    def fake_post(url, data=None, headers=None, timeout=None):
        if capture is not None:
            capture.update(
                {"url": url, "data": dict(data or {}), "headers": dict(headers or {})}
            )
        return SimpleNamespace(status_code=status, json=lambda: body)

    monkeypatch.setattr("httpx.post", fake_post)


def test_exchange_stores_broker_shaped_profile(secrets, monkeypatch):
    """The profile must be indistinguishable from the broker's (managed + refresh_token +
    expires) apart from `provider_mode`, or connector tools would treat it differently."""
    B.set_byo_config(secrets, "notion", {"client_id": "cid", "client_secret": "sec"})
    state = B.begin_byo_connect(secrets, "notion", redirect=REDIRECT)["state"]
    _patch_token(
        monkeypatch,
        {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
            "workspace_name": "WS",
            "workspace_id": "ws-1",
        },
    )
    res = B.complete_byo_connect(secrets, code="CODE", state=state)
    assert res["ok"] is True
    p = res["profile"]
    assert p["managed"] is True
    assert p["provider_mode"] == B.BYO_MODE
    assert p["access_token"] == "AT"
    assert p["refresh_token"] == "RT"
    assert p["expires"] > time.time()
    assert p["account"] == "WS" and p["account_id"] == "ws-1"


def test_notion_uses_basic_auth_and_keeps_secret_out_of_body(secrets, monkeypatch):
    B.set_byo_config(secrets, "notion", {"client_id": "cid", "client_secret": "sec"})
    state = B.begin_byo_connect(secrets, "notion", redirect=REDIRECT)["state"]
    cap: dict = {}
    _patch_token(monkeypatch, {"access_token": "AT"}, capture=cap)
    B.complete_byo_connect(secrets, code="CODE", state=state)
    assert cap["headers"]["Authorization"].startswith("Basic ")
    assert "client_secret" not in cap["data"]


def test_google_sends_credentials_in_body_with_verifier(secrets, monkeypatch):
    B.set_byo_config(secrets, "gmail", {"client_id": "gid", "client_secret": "gsec"})
    state = B.begin_byo_connect(secrets, "gmail", redirect=REDIRECT)["state"]
    cap: dict = {}
    _patch_token(monkeypatch, {"access_token": "AT"}, capture=cap)
    B.complete_byo_connect(secrets, code="CODE", state=state)
    assert cap["data"]["client_secret"] == "gsec"
    assert cap["data"]["grant_type"] == "authorization_code"
    assert cap["data"]["redirect_uri"] == REDIRECT
    assert cap["data"]["code_verifier"]  # PKCE round-trip


def test_exchange_rejects_unknown_state(secrets):
    res = B.complete_byo_connect(secrets, code="CODE", state="never-issued")
    assert res["ok"] is False and "expired" in res["error"]


def test_exchange_failure_is_clean(secrets, monkeypatch):
    B.set_byo_config(secrets, "notion", {"client_id": "cid", "client_secret": "sec"})
    state = B.begin_byo_connect(secrets, "notion", redirect=REDIRECT)["state"]
    _patch_token(monkeypatch, {}, status=400)
    res = B.complete_byo_connect(secrets, code="CODE", state=state)
    assert res["ok"] is False and "token exchange failed" in res["error"]


def test_slack_ok_false_is_treated_as_failure(secrets, monkeypatch):
    """Slack answers HTTP 200 with {"ok": false} instead of an error status."""
    B.set_byo_config(secrets, "slack", {"client_id": "cid", "client_secret": "sec"})
    state = B.begin_byo_connect(secrets, "slack", redirect=REDIRECT)["state"]
    _patch_token(monkeypatch, {"ok": False, "error": "invalid_code"})
    assert B.complete_byo_connect(secrets, code="CODE", state=state)["ok"] is False


# -- refresh --------------------------------------------------------------------
def test_refresh_keeps_old_refresh_token_when_provider_omits_it(secrets, monkeypatch):
    """Providers differ on rotating the refresh token; dropping it when absent kills the
    connection after a single rotation."""
    B.set_byo_config(secrets, "notion", {"client_id": "cid", "client_secret": "sec"})
    secrets.put(
        "notion:default",
        {
            "managed": True,
            "provider": "notion",
            "provider_mode": B.BYO_MODE,
            "access_token": "old",
            "refresh_token": "RT",
            "expires": time.time() - 1,
        },
    )
    _patch_token(monkeypatch, {"access_token": "new", "expires_in": 3600})
    out = B.refresh_byo_token(secrets, "notion")
    assert out is not None
    assert out["access_token"] == "new"
    assert out["refresh_token"] == "RT"
    assert secrets.get("notion:default")["access_token"] == "new"


def test_refresh_ignores_non_byo_profiles(secrets, monkeypatch):
    """A broker-managed profile must keep going to the broker, never here."""
    secrets.put(
        "notion:default",
        {"managed": True, "refresh_token": "RT", "access_token": "old"},
    )
    called = False

    def fake_post(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("must not call the provider for a broker profile")

    monkeypatch.setattr("httpx.post", fake_post)
    assert B.refresh_byo_token(secrets, "notion") is None
    assert called is False


def test_ensure_fresh_routes_byo_profiles_locally(secrets, monkeypatch):
    """The single refresh funnel connector tools use must dispatch on provider_mode, so a
    BYO connection refreshes without a cloud sign-in."""
    from coworker.cloud import ensure_fresh_connector_token
    from coworker.config import load_config

    B.set_byo_config(secrets, "notion", {"client_id": "cid", "client_secret": "sec"})
    secrets.put(
        "notion:default",
        {
            "managed": True,
            "provider": "notion",
            "provider_mode": B.BYO_MODE,
            "access_token": "old",
            "refresh_token": "RT",
            "expires": time.time() - 1,
        },
    )
    _patch_token(monkeypatch, {"access_token": "rotated", "expires_in": 3600})
    ensure_fresh_connector_token(secrets, load_config(), "notion")
    assert secrets.get("notion:default")["access_token"] == "rotated"


# -- GitHub App -----------------------------------------------------------------
@pytest.fixture
def rsa_pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key, key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()


def test_github_rejects_malformed_private_key(secrets):
    """Validate at save time so a bad paste fails in the form, not later at mint time."""
    res = G.set_byo_github_config(secrets, {"app_id": "1", "private_key": "not-a-pem"})
    assert res["ok"] is False and "private key" in res["error"]
    assert G.byo_github_available(secrets) is False


def test_github_jwt_claims_match_github_requirements(secrets, rsa_pem):
    """GitHub rejects a future `iat` and an `exp` more than 10 minutes out."""
    import jwt as pyjwt

    key, pem = rsa_pem
    assert G.set_byo_github_config(secrets, {"app_id": "424242", "private_key": pem})[
        "ok"
    ]
    token = G.app_jwt(secrets)
    claims = pyjwt.decode(token, key.public_key(), algorithms=["RS256"])
    now = int(time.time())
    assert claims["iss"] == "424242"
    assert claims["iat"] <= now
    assert 0 < claims["exp"] - now <= 600


def test_github_blank_key_on_resubmit_keeps_stored_one(secrets, rsa_pem):
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})
    assert G.set_byo_github_config(secrets, {"app_id": "2"})["ok"] is True
    assert G.byo_github_available(secrets) is True
    assert G.byo_github_config(secrets)["app_id"] == "2"


def test_github_clear_wipes_config_and_cache(secrets, rsa_pem):
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})
    G._token_cache["9"] = ("tok", time.time() + 3600)
    assert G.set_byo_github_config(secrets, {"app_id": ""})["configured"] is False
    assert G.byo_github_available(secrets) is False
    assert G._token_cache == {}


def _patch_mint(monkeypatch, calls, expires_in=3600, status=201):
    import datetime

    expires_at = (
        datetime.datetime.fromtimestamp(time.time() + expires_in, datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    def fake_post(url, headers=None, timeout=None):
        calls.append(headers.get("Authorization", ""))
        return SimpleNamespace(
            status_code=status,
            json=lambda: {"token": f"ghs_{len(calls)}", "expires_at": expires_at},
        )

    monkeypatch.setattr("httpx.post", fake_post)


def test_installation_token_is_cached_until_expiry(secrets, rsa_pem, monkeypatch):
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})
    calls: list[str] = []
    _patch_mint(monkeypatch, calls)

    first = G.byo_installation_token(secrets, "99")
    assert first == "ghs_1"
    assert G.byo_installation_token(secrets, "99") == first  # served from cache
    assert len(calls) == 1
    assert calls[0].startswith("Bearer ")  # App JWT, not a PAT
    # force skips the cache (the 401 retry path), and a different installation is separate.
    assert G.byo_installation_token(secrets, "99", force=True) == "ghs_2"
    assert G.byo_installation_token(secrets, "100") == "ghs_3"


def test_installation_token_empty_when_unavailable(secrets, rsa_pem, monkeypatch):
    """No App configured, a revoked installation, or a network failure must all degrade to
    "" rather than raising mid-turn."""
    assert G.byo_installation_token(secrets, "99") == ""

    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})
    assert G.byo_installation_token(secrets, "") == ""

    _patch_mint(monkeypatch, [], status=404)
    assert G.byo_installation_token(secrets, "99") == ""

    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("httpx.post", boom)
    G._token_cache.clear()
    assert G.byo_installation_token(secrets, "99") == ""


def test_expiry_parse_falls_back_to_an_hour(secrets):
    """A format change must shorten the cache, never make a token look valid forever."""
    assert G._parse_expiry("2030-01-01T00:00:00Z") == pytest.approx(
        1893456000, abs=86400
    )
    assert G._parse_expiry("garbage") > time.time()
    assert G._parse_expiry(None) > time.time()


# -- REST surface ---------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from coworker.server.app import create_app
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    B._pending.clear()
    G._token_cache.clear()
    manager = SessionManager(data_dir=tmp_path / "data")
    return TestClient(create_app(manager)), manager


def test_byo_status_never_leaks_the_secret(client):
    """The Settings pane reads this; a client secret in the response would put it in every
    GUI fetch and in any log that captures one."""
    c, _ = client
    assert c.get("/v1/connectors/byo").json() == {
        "oauth": {},
        "github": {"configured": False, "app_id": ""},
    }
    c.post(
        "/v1/connectors/notion/byo-config",
        json={"fields": {"client_id": "cid-x", "client_secret": "SECRET-x"}},
    )
    body = c.get("/v1/connectors/byo").text
    assert "cid-x" in body
    assert "SECRET-x" not in body


def test_byo_connect_returns_provider_consent_url(client):
    c, _ = client
    c.post(
        "/v1/connectors/notion/byo-config",
        json={"fields": {"client_id": "cid", "client_secret": "sec"}},
    )
    res = c.post("/v1/connectors/notion/byo-connect").json()
    assert res["ok"] is True
    assert res["authorize_url"].startswith("https://api.notion.com/v1/oauth/authorize?")


def test_byo_connect_rejects_connector_without_a_byo_path(client):
    c, _ = client
    assert c.post("/v1/connectors/datadog/byo-connect").json()["ok"] is False


def test_get_callback_completes_the_connection(client, monkeypatch):
    """The provider redirects the browser here with ?code=; the broker POSTs to the same
    path. Both must work, split by method."""
    c, manager = client
    c.post(
        "/v1/connectors/notion/byo-config",
        json={"fields": {"client_id": "cid", "client_secret": "sec"}},
    )
    url = c.post("/v1/connectors/notion/byo-connect").json()["authorize_url"]
    state = _query(url)["state"]
    _patch_token(
        monkeypatch,
        {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
            "workspace_id": "ws-1",
            "workspace_name": "WS",
        },
    )
    resp = c.get(f"/oauth/callback?code=CODE&state={state}")
    assert resp.status_code == 200
    assert "connected" in resp.text.lower()
    # Notion is account-keyed, so the token lands on the account profile and :default
    # becomes a pointer — the same shape the managed path produces.
    stored = manager.secrets.get("notion:account:ws-1") or {}
    assert stored["access_token"] == "AT"
    assert stored["provider_mode"] == B.BYO_MODE


def test_get_callback_rejects_reused_state_and_provider_error(client, monkeypatch):
    c, _ = client
    c.post(
        "/v1/connectors/notion/byo-config",
        json={"fields": {"client_id": "cid", "client_secret": "sec"}},
    )
    url = c.post("/v1/connectors/notion/byo-connect").json()["authorize_url"]
    state = _query(url)["state"]
    _patch_token(monkeypatch, {"access_token": "AT"})
    assert c.get(f"/oauth/callback?code=CODE&state={state}").status_code == 200
    assert c.get(f"/oauth/callback?code=CODE&state={state}").status_code == 400
    assert c.get("/oauth/callback?error=access_denied").status_code == 400


def test_github_byo_config_via_rest(client, rsa_pem):
    c, _ = client
    _, pem = rsa_pem
    assert (
        c.post(
            "/v1/connectors/github/byo-config",
            json={"fields": {"app_id": "777", "private_key": pem}},
        ).json()["ok"]
        is True
    )
    gh = c.get("/v1/connectors/byo").json()["github"]
    assert gh == {"configured": True, "app_id": "777"}
    assert pem not in c.get("/v1/connectors/byo").text
