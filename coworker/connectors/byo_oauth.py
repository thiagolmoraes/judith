"""Bring-your-own OAuth — the one-click connect flow driven by *your* OAuth app.

The managed path in ``cloud.py`` runs every step through Judith's broker: it builds the
consent URL (``/v1/oauth/<provider>/start``), exchanges the code, and rotates refresh tokens.
That needs a cloud sign-in, and the client secret lives on their server. This module is the
same flow with the broker removed: you register an OAuth app with the provider once, put its
credentials in the local config, and the desktop talks to the provider directly.

What this buys over the manual token path: no long-lived token pasted into a form, real
browser consent, automatic refresh, and an agent identity you own. What it costs: registering
an app yourself, and being the one who holds the client secret.

Profiles written here are shaped exactly like the broker's (``managed`` + ``refresh_token`` +
``expires``), so connector tools and session gating cannot tell the paths apart — with one
marker, ``provider_mode: "byo"``, which routes refresh back here instead of to the cloud.

GitHub is deliberately not an OAuth-app flow: a GitHub *App* authenticates by signing a JWT
with its private key and exchanging that for a short-lived installation token. That lives in
``byo_github.py``; this module covers the plain authorization-code providers.

Config lives under the ``byo_oauth`` SecretStore profile, one entry per connector::

    {
      "notion": {"client_id": "...", "client_secret": "..."},
      "gmail":  {"client_id": "...", "client_secret": "...",
                 "scopes": ["https://www.googleapis.com/auth/gmail.modify"]}
    }
"""

from __future__ import annotations

import base64
import hashlib
import secrets as _secrets
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlencode

from ..secrets import SecretStore
from ..i18n import t

BYO_PROFILE = "byo_oauth"
# Marks a profile as refreshed locally rather than through the cloud broker.
BYO_MODE = "byo"

_PENDING_TTL = 600
# state -> {"connector", "verifier", "redirect", "created"}; in-process only, like the
# broker's own pending map. A consent that outlives the sidecar is simply restarted.
_pending: dict[str, dict[str, Any]] = {}


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class ProviderSpec:
    """The provider-specific half of an authorization-code flow."""

    authorize_url: str
    token_url: str
    default_scopes: tuple[str, ...] = ()
    # Providers that reject a scope-less request, or whose token endpoint needs the client
    # credentials in an Authorization: Basic header rather than the form body.
    basic_auth: bool = False
    # PKCE is required by some providers and harmless everywhere else, so it is on by
    # default; the few that reject the extra params opt out.
    pkce: bool = True
    # Extra fixed params on the consent URL (e.g. Google's offline access + consent prompt,
    # without which no refresh_token is issued on re-consent).
    extra_authorize: dict[str, str] = field(default_factory=dict)


# Endpoints verified against each provider's current OAuth documentation. Scopes are the
# minimum the corresponding connector tools need; override per connector in the config.
PROVIDERS: dict[str, ProviderSpec] = {
    "google": ProviderSpec(
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        extra_authorize={"access_type": "offline", "prompt": "consent"},
    ),
    "microsoft": ProviderSpec(
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        default_scopes=("offline_access",),
    ),
    "notion": ProviderSpec(
        authorize_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        # Notion authenticates the token call with Basic and rejects PKCE params.
        basic_auth=True,
        pkce=False,
        extra_authorize={"owner": "user"},
    ),
    "slack": ProviderSpec(
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        pkce=False,
    ),
    "hubspot": ProviderSpec(
        authorize_url="https://app.hubspot.com/oauth/authorize",
        token_url="https://api.hubapi.com/oauth/v1/token",
        pkce=False,
    ),
    "attio": ProviderSpec(
        authorize_url="https://app.attio.com/authorize",
        token_url="https://app.attio.com/oauth/token",
        pkce=False,
    ),
}

# Which provider each connector talks to. Mirrors cloud.PROVIDER_FOR_CONNECTOR so a
# connector behaves the same whichever path configured it.
CONNECTOR_PROVIDER: dict[str, str] = {
    "gmail": "google",
    "google_calendar": "google",
    "google_drive": "google",
    "outlook": "microsoft",
    "notion": "notion",
    "slack": "slack",
    "hubspot": "hubspot",
    "attio": "attio",
}

# Scopes a connector needs when the config doesn't name any. Deliberately narrow: a
# read-heavy default is easier to widen than to explain after the fact.
DEFAULT_SCOPES: dict[str, tuple[str, ...]] = {
    "gmail": ("https://www.googleapis.com/auth/gmail.modify",),
    "google_calendar": ("https://www.googleapis.com/auth/calendar",),
    "google_drive": ("https://www.googleapis.com/auth/drive",),
    "outlook": ("Mail.ReadWrite", "Mail.Send", "offline_access"),
    "hubspot": ("crm.objects.contacts.read",),
    "slack": ("channels:history", "channels:read", "chat:write", "users:read"),
}


def byo_config(secrets: SecretStore, connector: str) -> dict[str, Any]:
    """This connector's BYO app credentials, or {} when none are configured."""
    store = secrets.get(BYO_PROFILE) or {}
    entry = store.get(connector)
    return dict(entry) if isinstance(entry, dict) else {}


def byo_available(secrets: SecretStore, connector: str) -> bool:
    """Whether a browser consent flow can run for `connector` without a cloud sign-in."""
    if connector not in CONNECTOR_PROVIDER:
        return False
    cfg = byo_config(secrets, connector)
    return bool(cfg.get("client_id") and cfg.get("client_secret"))


def set_byo_config(
    secrets: SecretStore, connector: str, fields: dict[str, Any]
) -> dict[str, Any]:
    """Store (or clear) one connector's BYO app credentials.

    A blank client_id removes the entry, which is how the GUI turns BYO back off. Scopes
    accept a list or a whitespace/comma-separated string, since both shapes show up in
    provider dashboards.
    """
    if connector not in CONNECTOR_PROVIDER:
        return {"ok": False, "error": t("error.noByoPath", connector=connector)}
    store = dict(secrets.get(BYO_PROFILE) or {})
    client_id = str(fields.get("client_id") or "").strip()
    if not client_id:
        store.pop(connector, None)
        secrets.put(BYO_PROFILE, store)
        return {"ok": True, "configured": False}
    secret = str(fields.get("client_secret") or "").strip()
    if not secret:
        # Reconnect-safe, mirroring connect_connector: a blank secret on re-submit means
        # "keep the stored one" rather than wiping a working credential.
        secret = str((store.get(connector) or {}).get("client_secret") or "").strip()
    if not secret:
        return {"ok": False, "error": t("error.clientSecretRequired")}
    entry: dict[str, Any] = {"client_id": client_id, "client_secret": secret}
    # Normalize to a list of strings here rather than trusting the caller: anything else
    # (a dict, a list of ints) survives the save and only blows up later inside
    # `" ".join(...)`, turning a bad save into a 500 on the connect endpoint.
    scopes = fields.get("scopes")
    if isinstance(scopes, str):
        scopes = scopes.replace(",", " ").split()
    if isinstance(scopes, (list, tuple, set)):
        cleaned = [str(s).strip() for s in scopes if str(s).strip()]
        if cleaned:
            entry["scopes"] = list(dict.fromkeys(cleaned))
    elif scopes:
        return {"ok": False, "error": t("error.scopesShape")}
    store[connector] = entry
    secrets.put(BYO_PROFILE, store)
    return {"ok": True, "configured": True}


def _scopes_for(secrets: SecretStore, connector: str, spec: ProviderSpec) -> list[str]:
    cfg = byo_config(secrets, connector)
    scopes = cfg.get("scopes") or list(DEFAULT_SCOPES.get(connector, ()))
    merged = list(dict.fromkeys([*spec.default_scopes, *scopes]))
    return merged


def begin_byo_connect(
    secrets: SecretStore, connector: str, *, redirect: str
) -> dict[str, Any]:
    """Build the provider consent URL for the browser. No cloud sign-in involved.

    `redirect` must be the loopback URL registered with the provider app; it is echoed
    back on the callback and re-sent at token exchange, as the spec requires.
    """
    provider = CONNECTOR_PROVIDER.get(connector)
    if provider is None:
        return {"ok": False, "error": t("error.noByoPath", connector=connector)}
    spec = PROVIDERS.get(provider)
    if spec is None:
        return {"ok": False, "error": f"no BYO endpoints known for {provider}"}
    cfg = byo_config(secrets, connector)
    if not cfg.get("client_id"):
        return {"ok": False, "error": t("error.noByoAppConfigured", connector=connector)}

    state = _secrets.token_urlsafe(24)
    params: dict[str, str] = {
        "client_id": cfg["client_id"],
        "redirect_uri": redirect,
        "response_type": "code",
        "state": state,
        **spec.extra_authorize,
    }
    scopes = _scopes_for(secrets, connector, spec)
    if scopes:
        params["scope"] = " ".join(scopes)
    verifier = ""
    if spec.pkce:
        verifier = _secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        params["code_challenge"] = challenge
        params["code_challenge_method"] = "S256"

    _prune_pending()
    _pending[state] = {
        "connector": connector,
        "verifier": verifier,
        "redirect": redirect,
        "created": _now(),
    }
    return {
        "ok": True,
        "authorize_url": f"{spec.authorize_url}?{urlencode(params)}",
        "state": state,
    }


def _prune_pending() -> None:
    cutoff = _now() - _PENDING_TTL
    for state in [s for s, d in _pending.items() if float(d["created"]) < cutoff]:
        _pending.pop(state, None)


def consume_byo_state(state: str) -> Optional[dict[str, Any]]:
    """Single-use lookup of a pending consent. None when unknown or expired."""
    _prune_pending()
    return _pending.pop(str(state or ""), None)


def _token_request(
    spec: ProviderSpec, cfg: dict[str, Any], form: dict[str, str]
) -> dict[str, Any]:
    """POST to the provider's token endpoint. Returns {} on any failure — callers turn
    that into a user-facing error, and a token endpoint's body can carry the secret."""
    import httpx

    headers = {"Accept": "application/json"}
    if spec.basic_auth:
        raw = f"{cfg['client_id']}:{cfg['client_secret']}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    else:
        form = {
            **form,
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        }
    try:
        resp = httpx.post(spec.token_url, data=form, headers=headers, timeout=20)
    except httpx.HTTPError:
        return {}
    if resp.status_code != 200:
        return {}
    try:
        body = resp.json()
    except ValueError:
        return {}
    if not isinstance(body, dict):
        return {}
    # Slack answers 200 with {"ok": false, "error": ...} instead of an HTTP error.
    if body.get("ok") is False:
        return {}
    return body


def _profile_from_token(
    connector: str, provider: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Shape a token response like the broker's profiles so tools can't tell them apart."""
    # Slack nests the bot token; every other provider here is flat. Every nested lookup is
    # type-guarded — a provider response is untrusted input, and an unexpected shape must
    # not raise AttributeError, which would surface as a 500 on the browser callback.
    authed = (
        body.get("authed_user") if isinstance(body.get("authed_user"), dict) else {}
    )
    bot = body.get("bot") if isinstance(body.get("bot"), dict) else {}
    access = (
        body.get("access_token")
        or bot.get("bot_access_token")
        or authed.get("access_token")
        or ""
    )
    expires_in = body.get("expires_in")
    profile: dict[str, Any] = {
        "managed": True,
        "provider": provider,
        "provider_mode": BYO_MODE,
        "access_token": str(access),
    }
    if body.get("refresh_token"):
        profile["refresh_token"] = str(body["refresh_token"])
    if expires_in:
        try:
            profile["expires"] = _now() + int(expires_in) - 60
        except (TypeError, ValueError):
            pass
    team = body.get("team") if isinstance(body.get("team"), dict) else {}
    owner = body.get("owner") if isinstance(body.get("owner"), dict) else {}
    owner_user = owner.get("user") if isinstance(owner.get("user"), dict) else {}
    account = (
        body.get("account")
        or body.get("workspace_name")
        or team.get("name")
        or owner_user.get("name")
    )
    if account:
        profile["account"] = str(account)
    if body.get("workspace_id") or team.get("id"):
        profile["account_id"] = str(body.get("workspace_id") or team.get("id"))
    return profile


def complete_byo_connect(
    secrets: SecretStore, *, code: str, state: str
) -> dict[str, Any]:
    """Exchange an authorization code for tokens and store the connector profile."""
    pending = consume_byo_state(state)
    if pending is None:
        return {"ok": False, "error": t("error.unknownConnectionAttempt")}
    connector = str(pending["connector"])
    provider = CONNECTOR_PROVIDER.get(connector, "")
    spec = PROVIDERS.get(provider)
    cfg = byo_config(secrets, connector)
    if spec is None or not cfg.get("client_id"):
        return {"ok": False, "error": t("error.noByoAppConfigured", connector=connector)}
    if not code:
        return {"ok": False, "error": "missing authorization code"}

    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": str(pending["redirect"]),
    }
    if pending.get("verifier"):
        form["code_verifier"] = str(pending["verifier"])
    body = _token_request(spec, cfg, form)
    if not body:
        return {"ok": False, "error": t("error.tokenExchangeFailed")}
    profile = _profile_from_token(connector, provider, body)
    if not profile.get("access_token"):
        return {"ok": False, "error": t("error.noAccessToken")}
    return {"ok": True, "connector": connector, "profile": profile}


def refresh_byo_token(
    secrets: SecretStore, connector: str, *, profile_key: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """Rotate a BYO profile's access token in place. None when it can't be refreshed.

    Providers differ on whether a refresh returns a new refresh_token; keep the old one
    when it doesn't, or the connection dies after a single rotation.
    """
    key = profile_key or f"{connector}:default"
    profile = secrets.get(key) or {}
    if profile.get("provider_mode") != BYO_MODE:
        return None
    refresh = str(profile.get("refresh_token") or "")
    if not refresh:
        return None
    provider = str(profile.get("provider") or CONNECTOR_PROVIDER.get(connector, ""))
    spec = PROVIDERS.get(provider)
    cfg = byo_config(secrets, connector)
    if spec is None or not cfg.get("client_id"):
        return None
    body = _token_request(
        spec, cfg, {"grant_type": "refresh_token", "refresh_token": refresh}
    )
    if not body or not body.get("access_token"):
        return None
    profile["access_token"] = str(body["access_token"])
    if body.get("refresh_token"):
        profile["refresh_token"] = str(body["refresh_token"])
    if body.get("expires_in"):
        try:
            profile["expires"] = _now() + int(body["expires_in"]) - 60
        except (TypeError, ValueError):
            pass
    secrets.put(key, profile)
    return profile
