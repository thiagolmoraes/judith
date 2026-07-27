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
    assert cap["url"] == B.PROVIDERS["notion"].token_url
    assert cap["headers"]["Authorization"].startswith("Basic ")
    assert "client_secret" not in cap["data"]


def test_google_sends_credentials_in_body_with_verifier(secrets, monkeypatch):
    B.set_byo_config(secrets, "gmail", {"client_id": "gid", "client_secret": "gsec"})
    state = B.begin_byo_connect(secrets, "gmail", redirect=REDIRECT)["state"]
    cap: dict = {}
    _patch_token(monkeypatch, {"access_token": "AT"}, capture=cap)
    B.complete_byo_connect(secrets, code="CODE", state=state)
    assert cap["url"] == B.PROVIDERS["google"].token_url
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
@pytest.fixture(scope="module")
def rsa_pem():
    """Module-scoped: the key is immutable and shared by several tests, and generating
    RSA-2048 per test is by far the slowest thing in this file."""
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

    def fake_request(method, url, headers=None, params=None, timeout=None):
        calls.append(headers.get("Authorization", ""))
        return SimpleNamespace(
            status_code=status,
            json=lambda: {"token": f"ghs_{len(calls)}", "expires_at": expires_at},
        )

    monkeypatch.setattr("httpx.request", fake_request)


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

    monkeypatch.setattr("httpx.request", boom)
    monkeypatch.setattr("time.sleep", lambda _s: None)  # don't wait out the backoff
    G._token_cache.clear()
    assert G.byo_installation_token(secrets, "99") == ""


def test_transient_failures_are_retried_but_hard_failures_are_not(
    secrets, rsa_pem, monkeypatch
):
    """A 429/5xx is worth another attempt; a 404 is a real answer (revoked installation)
    and retrying it only delays the failure."""
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})
    monkeypatch.setattr("time.sleep", lambda _s: None)

    statuses = [503, 429, 201]
    attempts: list[int] = []

    def flaky(method, url, headers=None, params=None, timeout=None):
        code = statuses[len(attempts)]
        attempts.append(code)
        return SimpleNamespace(
            status_code=code, json=lambda: {"token": "ghs_ok", "expires_at": ""}
        )

    monkeypatch.setattr("httpx.request", flaky)
    assert G.byo_installation_token(secrets, "99") == "ghs_ok"
    assert attempts == [503, 429, 201]

    hard: list[int] = []

    def denied(method, url, headers=None, params=None, timeout=None):
        hard.append(404)
        return SimpleNamespace(status_code=404, json=lambda: {})

    monkeypatch.setattr("httpx.request", denied)
    G._token_cache.clear()
    assert G.byo_installation_token(secrets, "99") == ""
    assert len(hard) == 1  # no retry on a definitive answer


def test_retries_give_up_after_the_bound(secrets, rsa_pem, monkeypatch):
    """Retries are bounded — a provider stuck on 503 must not retry forever."""
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})
    monkeypatch.setattr("time.sleep", lambda _s: None)
    calls: list[int] = []

    def always_503(method, url, headers=None, params=None, timeout=None):
        calls.append(503)
        return SimpleNamespace(status_code=503, json=lambda: {})

    monkeypatch.setattr("httpx.request", always_503)
    assert G.byo_installation_token(secrets, "99") == ""
    assert len(calls) == G._ATTEMPTS


def test_non_object_json_does_not_raise(secrets, rsa_pem, monkeypatch):
    """Valid JSON can be a list or a string; `.get()` on those would raise, breaking the
    fail-safe contract these functions promise."""
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})

    def listy(method, url, headers=None, params=None, timeout=None):
        return SimpleNamespace(status_code=200, json=lambda: ["not", "an", "object"])

    monkeypatch.setattr("httpx.request", listy)
    G._token_cache.clear()
    assert G.byo_installation_token(secrets, "99") == ""
    assert G.app_slug(secrets) is None


def test_list_installations_maps_accounts_and_degrades(secrets, rsa_pem, monkeypatch):
    """Backs GET /v1/connectors/github/byo-installations — the connect picker."""
    # No App configured at all: an empty list, never an error.
    assert G.list_byo_installations(secrets) == []

    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})

    def listing(method, url, headers=None, params=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            json=lambda: [
                {"id": 11, "account": {"login": "acme", "type": "Organization"}},
                {"id": 12, "account": {"login": "me", "type": "User"}},
                {"account": {"login": "no-id"}},  # dropped: unusable without an id
                "junk",  # dropped: not an object
            ],
        )

    monkeypatch.setattr("httpx.request", listing)
    assert G.list_byo_installations(secrets) == [
        {"installation_id": "11", "account": "acme", "account_type": "Organization"},
        {"installation_id": "12", "account": "me", "account_type": "User"},
    ]


def test_install_url_built_from_app_slug(secrets, rsa_pem, monkeypatch):
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})

    def app_meta(method, url, headers=None, params=None, timeout=None):
        return SimpleNamespace(status_code=200, json=lambda: {"slug": "my-agent"})

    monkeypatch.setattr("httpx.request", app_meta)
    assert (
        G.install_url(secrets) == "https://github.com/apps/my-agent/installations/new"
    )


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
    # The connect route opens the system browser; a test must never actually launch one.
    monkeypatch.setattr("webbrowser.open", lambda _url: True)
    B._pending.clear()
    G._token_cache.clear()
    manager = SessionManager(data_dir=tmp_path / "data")
    return TestClient(create_app(manager)), manager


def test_byo_status_never_leaks_the_secret(client):
    """The Settings pane reads this; a client secret in the response would put it in every
    GUI fetch and in any log that captures one."""
    c, _ = client
    empty = c.get("/v1/connectors/byo").json()
    assert empty["oauth"] == {}
    assert empty["github"] == {"configured": False, "app_id": ""}
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


def test_byo_installations_endpoint(client, rsa_pem, monkeypatch):
    """The picker endpoint: an empty list when no App is configured, never a 500."""
    c, _ = client
    empty = c.get("/v1/connectors/github/byo-installations").json()
    assert empty == {"ok": True, "installations": []}

    _, pem = rsa_pem
    c.post(
        "/v1/connectors/github/byo-config",
        json={"fields": {"app_id": "1", "private_key": pem}},
    )

    def listing(method, url, headers=None, params=None, timeout=None):
        return SimpleNamespace(
            status_code=200,
            json=lambda: [
                {"id": 7, "account": {"login": "acme", "type": "Organization"}}
            ],
        )

    monkeypatch.setattr("httpx.request", listing)
    body = c.get("/v1/connectors/github/byo-installations").json()
    assert body["installations"] == [
        {"installation_id": "7", "account": "acme", "account_type": "Organization"}
    ]


def test_byo_config_rejects_non_object_fields(client):
    """`fields or {}` only guards None; a list or string would reach .get() and 500."""
    c, _ = client
    for bad in (["a"], "x", 3):
        res = c.post("/v1/connectors/notion/byo-config", json={"fields": bad}).json()
        assert res["ok"] is False, bad
        assert "object" in res["error"]


def test_byo_config_rejects_malformed_scopes(client):
    c, _ = client
    res = c.post(
        "/v1/connectors/notion/byo-config",
        json={"fields": {"client_id": "c", "client_secret": "s", "scopes": {"a": 1}}},
    ).json()
    assert res["ok"] is False
    assert "scopes" in res["error"]


def test_callback_survives_a_failing_gateway(client, monkeypatch):
    """The profile is stored before the gateway reload; a listener that fails to come up
    must not turn a successful connect into a 500 with the token already saved."""
    c, manager = client
    c.post(
        "/v1/connectors/notion/byo-config",
        json={"fields": {"client_id": "cid", "client_secret": "sec"}},
    )
    url = c.post("/v1/connectors/notion/byo-connect").json()["authorize_url"]
    state = _query(url)["state"]
    _patch_token(monkeypatch, {"access_token": "AT", "workspace_id": "ws-1"})

    async def boom():
        raise RuntimeError("listener down")

    monkeypatch.setattr(manager, "refresh_gateway", boom)
    resp = c.get(f"/oauth/callback?code=CODE&state={state}")
    assert resp.status_code == 200
    assert (manager.secrets.get("notion:account:ws-1") or {})["access_token"] == "AT"


def test_callback_page_uses_the_display_title(client, monkeypatch):
    """ "Notion connected", not "notion connected" — matching the managed callback."""
    c, _ = client
    c.post(
        "/v1/connectors/notion/byo-config",
        json={"fields": {"client_id": "cid", "client_secret": "sec"}},
    )
    url = c.post("/v1/connectors/notion/byo-connect").json()["authorize_url"]
    state = _query(url)["state"]
    _patch_token(monkeypatch, {"access_token": "AT", "workspace_id": "ws-1"})
    assert "Notion connected" in c.get(f"/oauth/callback?code=CODE&state={state}").text


def test_list_installations_paginates(secrets, rsa_pem, monkeypatch):
    """A picker showing only the first page would make a real installation look missing."""
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})
    pages: list[int] = []

    def paged(method, url, headers=None, params=None, timeout=None):
        page = int((params or {}).get("page", 1))
        pages.append(page)
        # Two full pages, then a short one that ends the walk.
        body = (
            [
                {"id": 100 + page * 100 + i, "account": {"login": f"a{page}"}}
                for i in range(100)
            ]
            if page < 3
            else [{"id": 999, "account": {"login": "last"}}]
        )
        return SimpleNamespace(status_code=200, json=lambda: body)

    monkeypatch.setattr("httpx.request", paged)
    out = G.list_byo_installations(secrets)
    assert pages == [1, 2, 3]  # stops on the short page, doesn't keep walking
    assert len(out) == 201
    assert out[-1]["account"] == "last"


def test_pagination_is_bounded(secrets, rsa_pem, monkeypatch):
    """A server that always returns a full page must not loop forever."""
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})
    pages: list[int] = []

    def endless(method, url, headers=None, params=None, timeout=None):
        pages.append(int((params or {}).get("page", 1)))
        body = [{"id": i, "account": {"login": "x"}} for i in range(1, 101)]
        return SimpleNamespace(status_code=200, json=lambda: body)

    monkeypatch.setattr("httpx.request", endless)
    G.list_byo_installations(secrets)
    assert len(pages) == G._MAX_PAGES


def test_partial_pagination_keeps_what_it_has(secrets, rsa_pem, monkeypatch):
    """A page that fails mid-walk returns the installations already collected, rather than
    discarding them and looking like the App has none."""
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def flaky(method, url, headers=None, params=None, timeout=None):
        if int((params or {}).get("page", 1)) == 1:
            # ids 0..99: a full page for the pagination walk, and id 0 exercises the
            # unusable-id filter, so only 99 survive.
            body = [{"id": i, "account": {"login": "x"}} for i in range(100)]
            return SimpleNamespace(status_code=200, json=lambda: body)
        return SimpleNamespace(status_code=404, json=lambda: {})

    monkeypatch.setattr("httpx.request", flaky)
    # 100 returned, minus id 0 which is correctly dropped as unusable.
    assert len(G.list_byo_installations(secrets)) == 99


def test_truncated_listing_is_logged(secrets, rsa_pem, monkeypatch, caplog):
    """Hitting the page cap must say so: a silently truncated picker reads as "everything
    is here" when it isn't."""
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})

    def endless(method, url, headers=None, params=None, timeout=None):
        body = [{"id": i, "account": {"login": "x"}} for i in range(1, 101)]
        return SimpleNamespace(status_code=200, json=lambda: body)

    monkeypatch.setattr("httpx.request", endless)
    with caplog.at_level("WARNING", logger="coworker.connectors"):
        G.list_byo_installations(secrets)
    assert any("cap" in r.message for r in caplog.records)


def test_failed_page_is_logged(secrets, rsa_pem, monkeypatch, caplog):
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})
    monkeypatch.setattr("time.sleep", lambda _s: None)

    def flaky(method, url, headers=None, params=None, timeout=None):
        if int((params or {}).get("page", 1)) == 1:
            body = [{"id": i, "account": {"login": "x"}} for i in range(1, 101)]
            return SimpleNamespace(status_code=200, json=lambda: body)
        return SimpleNamespace(status_code=404, json=lambda: {})

    monkeypatch.setattr("httpx.request", flaky)
    with caplog.at_level("WARNING", logger="coworker.connectors"):
        out = G.list_byo_installations(secrets)
    assert len(out) == 100
    assert any("failed" in r.message for r in caplog.records)


def test_complete_listing_logs_nothing(secrets, rsa_pem, monkeypatch, caplog):
    """A listing that finished cleanly must stay quiet — a warning on every fetch would
    train the user to ignore the one that matters."""
    _, pem = rsa_pem
    G.set_byo_github_config(secrets, {"app_id": "1", "private_key": pem})

    def short(method, url, headers=None, params=None, timeout=None):
        return SimpleNamespace(
            status_code=200, json=lambda: [{"id": 5, "account": {"login": "x"}}]
        )

    monkeypatch.setattr("httpx.request", short)
    with caplog.at_level("WARNING", logger="coworker.connectors"):
        assert len(G.list_byo_installations(secrets)) == 1
    assert caplog.records == []


def test_connect_opens_the_system_browser(client, monkeypatch):
    """The sidecar opens the browser itself, matching /v1/cloud/login and the managed
    connect — the GUI only polls afterwards, so it never sees the URL."""
    c, _ = client
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    c.post(
        "/v1/connectors/notion/byo-config",
        json={"fields": {"client_id": "cid", "client_secret": "sec"}},
    )
    res = c.post("/v1/connectors/notion/byo-connect").json()
    assert res["ok"] is True
    assert opened == [res["authorize_url"]]


def test_failed_connect_opens_nothing(client, monkeypatch):
    """No app configured: report the error rather than launching a browser at a URL that
    was never built."""
    c, _ = client
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    assert c.post("/v1/connectors/notion/byo-connect").json()["ok"] is False
    assert opened == []


def test_status_reports_the_real_redirect_uri(client, monkeypatch):
    """The GUI tells the user which redirect to register, and the packaged sidecar binds a
    random port — so the value has to come from the server, and has to be the same one used
    at consent time or the provider rejects it."""
    c, _ = client
    monkeypatch.setenv("COWORKER_PORT", "50055")
    reported = c.get("/v1/connectors/byo").json()["redirect_uri"]
    assert reported == "http://127.0.0.1:50055/oauth/callback"

    c.post(
        "/v1/connectors/notion/byo-config",
        json={"fields": {"client_id": "cid", "client_secret": "sec"}},
    )
    url = c.post("/v1/connectors/notion/byo-connect").json()["authorize_url"]
    assert _query(url)["redirect_uri"] == reported
