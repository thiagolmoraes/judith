"""The cloud switch: with `cloud_enabled` off (the default), nothing reaches OpenWorker
Cloud and the GUI is told not to offer sign-in.

The point of the flag is that a local-only install is local-only *provably*, not by the
user happening never to sign in — so these tests assert the negative: no token, no
telemetry, no managed connect, even when a session is already stored.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from coworker.cloud import (
    CLOUD_AUTH_PROFILE,
    begin_login,
    emit_session_created,
    fresh_access_token,
)
from coworker.config import Config, load_config
from coworker.secrets import SecretStore
from coworker.server import SessionManager, create_app


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    return SecretStore(path=tmp_path / "state" / "secrets.json")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=tmp_path)
    with TestClient(create_app(manager)) as c:
        c.manager = manager
        yield c


def _signed_in(secrets: SecretStore) -> None:
    """A stored, unexpired cloud session — the state the flag has to override."""
    secrets.put(
        CLOUD_AUTH_PROFILE,
        {"access_token": "AT", "refresh_token": "RT", "expires": time.time() + 3600},
    )


# -- the default ----------------------------------------------------------------
def test_cloud_is_off_by_default():
    assert Config().cloud_enabled is False


def test_workspace_config_cannot_switch_the_cloud_on(tmp_path):
    """`cloud_enabled` is global-only: a cloned repository must not be able to turn on
    network access to a vendor on the user's behalf, the way a workspace can request
    (advisory) allowed_commands."""
    ws = tmp_path / "ws"
    (ws / ".coworker").mkdir(parents=True)
    (ws / ".coworker" / "config.toml").write_text("cloud_enabled = true\n")
    g = tmp_path / "global.toml"
    g.write_text("cloud_enabled = false\n")

    assert load_config(workspace=ws, global_path=g).cloud_enabled is False
    # Not even a trusted workspace, which *can* contribute allowed_commands.
    assert (
        load_config(workspace=ws, global_path=g, workspace_trusted=True).cloud_enabled
        is False
    )


def test_global_config_can_switch_it_on(tmp_path):
    g = tmp_path / "global.toml"
    g.write_text("cloud_enabled = true\n")
    assert load_config(global_path=g).cloud_enabled is True


# -- the chokepoint -------------------------------------------------------------
def test_no_token_even_with_a_valid_stored_session(secrets):
    """The single check every cloud caller inherits. A stored session must not be usable
    while the cloud is off, or turning the flag off would only hide the UI."""
    _signed_in(secrets)
    assert fresh_access_token(secrets, Config(cloud_enabled=False)) is None
    # ...and the session is still there, so flipping the flag back restores it.
    assert fresh_access_token(secrets, Config(cloud_enabled=True)) == "AT"


def test_login_refuses_before_generating_a_challenge():
    res = begin_login(Config(cloud_enabled=False))
    assert res["ok"] is False
    assert "disabled" in res["error"]
    assert "authorize_url" not in res


def test_telemetry_never_fires(secrets, monkeypatch):
    """Telemetry is default-on and gated on sign-in; with the cloud off it must be gated
    twice over. Any outbound POST here is a failure."""
    _signed_in(secrets)

    def boom(*a, **k):
        raise AssertionError("telemetry must not reach the network")

    monkeypatch.setattr("httpx.post", boom)
    assert (
        emit_session_created(
            secrets,
            Config(cloud_enabled=False),
            session_id="s1",
            persona_id="cowork",
            persona_family="cowork",
            workspace_kind="folder",
        )
        is False
    )


# -- what the GUI sees ----------------------------------------------------------
def test_status_reports_unavailable_not_merely_signed_out(client):
    body = client.get("/v1/cloud/status").json()
    assert body["enabled"] is False
    assert body["signed_in"] is False
    # Reported off rather than the stored preference: with no token nothing can be sent,
    # and showing "telemetry on" would misstate what the install actually does.
    assert body["telemetry_enabled"] is False


def test_status_hides_a_stored_session(client):
    """Someone who signed in before the flag was turned off must read as signed out, or
    the GUI would offer managed actions that the backend then refuses."""
    _signed_in(client.manager.secrets)
    body = client.get("/v1/cloud/status").json()
    assert body["enabled"] is False
    assert body["signed_in"] is False


def test_managed_connect_is_refused(client):
    _signed_in(client.manager.secrets)
    body = client.post("/v1/connectors/notion/connect-managed").json()
    assert body["ok"] is False


# -- what still works -----------------------------------------------------------
def test_local_paths_are_unaffected(client):
    """The flag must cost nothing locally: manual connect and the bring-your-own OAuth
    surface are the ways in once the cloud is off, so they have to keep working."""
    res = client.post(
        "/v1/connectors/github/connect", json={"fields": {"token": "ghp_x"}}
    ).json()
    assert res["ok"] is True

    byo = client.get("/v1/connectors/byo").json()
    assert "redirect_uri" in byo  # the BYO pane still has what it needs


def test_no_cloud_route_500s_when_disabled(tmp_path, monkeypatch):
    """Every cloud route must refuse cleanly with the cloud off.

    Regression: /v1/cloud/login read out["authorize_url"] unconditionally, so a refusal
    (which carries no such key) surfaced as a 500 with a traceback instead of a plain
    "unavailable". A per-route sweep is cheaper than remembering to check each new one.
    """
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("webbrowser.open", lambda _u: True)
    manager = SessionManager(workspace=tmp_path)
    # raise_server_exceptions=False so a handler crash shows up as 500 rather than
    # propagating into the test as the original exception.
    with TestClient(create_app(manager), raise_server_exceptions=False) as c:
        routes = [
            ("GET", "/v1/cloud/status"),
            ("POST", "/v1/cloud/login"),
            ("POST", "/v1/cloud/logout"),
            ("GET", "/v1/cloud/gallery"),
            ("GET", "/v1/cloud/gallery/some-slug"),
            ("POST", "/v1/cloud/telemetry"),
            ("POST", "/v1/connectors/notion/connect-managed"),
        ]
        # Every /v1/cloud/* route the app registers, so a new one added without a
        # disabled-path check shows up here rather than in the field.
        registered = {
            r.path
            for r in create_app(manager).routes
            if "/v1/cloud" in getattr(r, "path", "")
        }
        assert registered <= {p for _, p in routes} | {"/v1/cloud/gallery/{slug}"}, (
            f"cloud routes missing from the sweep: {registered - {p for _, p in routes}}"
        )
        for method, path in routes:
            # A body for the POSTs that take one, so the handler actually runs instead of
            # bouncing off request validation before reaching the disabled path.
            resp = c.request(method, path, json={} if method == "POST" else None)
            assert resp.status_code < 500, f"{method} {path} -> {resp.status_code}"


def test_login_route_refuses_without_opening_a_browser(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    manager = SessionManager(workspace=tmp_path)
    with TestClient(create_app(manager)) as c:
        body = c.post("/v1/cloud/login").json()
    assert body["ok"] is False
    assert "disabled" in body["error"]
    assert opened == []
