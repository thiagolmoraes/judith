"""Bring-your-own GitHub App — installation tokens minted locally, no broker.

A GitHub App doesn't authenticate with a client secret and a refresh token like the plain
OAuth providers in ``byo_oauth.py``. It signs a short-lived JWT with its own RSA private key,
then trades that JWT for an *installation* access token scoped to one installation. Those
tokens last an hour and are never stored at rest.

The managed path routes all three steps through OpenWorker's broker
(``cloud.github_installation_token``), which holds the ``ocw-agent`` App's private key. This
module does the same thing with a key you own, so the agent acts as *your* App and no cloud
sign-in is involved.

Config lives under the ``byo_github`` SecretStore profile::

    {"app_id": "123456", "private_key": "-----BEGIN RSA PRIVATE KEY-----\\n..."}

The private key is the App's full authority — it can mint a token for every installation the
App has. It is stored in the SecretStore like any other credential (user-only file mode, see
``secrets.py``), but treat it with the care you'd give an SSH key.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from cryptography.exceptions import UnsupportedAlgorithm

from ..secrets import SecretStore
from ..i18n import t

logger = logging.getLogger("coworker.connectors")

BYO_GITHUB_PROFILE = "byo_github"

_API = "https://api.github.com"
# GitHub rejects a JWT whose `iat` is in the future by even a second, so back-date it to
# absorb clock skew; `exp` must be at most 10 minutes out.
_JWT_SKEW = 60
_JWT_TTL = 540

# installation_id -> (token, expires_at). Memory-only, mirroring the managed path: an
# installation token is a live credential and never belongs at rest.
_token_cache: dict[str, tuple[str, float]] = {}
_CACHE_LEEWAY = 120


def _now() -> float:
    return time.time()


def byo_github_config(secrets: SecretStore) -> dict[str, Any]:
    profile = secrets.get(BYO_GITHUB_PROFILE) or {}
    return dict(profile) if isinstance(profile, dict) else {}


def byo_github_available(secrets: SecretStore) -> bool:
    """Whether installation tokens can be minted locally (App id + private key present)."""
    cfg = byo_github_config(secrets)
    return bool(cfg.get("app_id") and cfg.get("private_key"))


def set_byo_github_config(
    secrets: SecretStore, fields: dict[str, Any]
) -> dict[str, Any]:
    """Store (or clear) the BYO GitHub App credentials.

    A blank app_id clears the config. A blank private_key on re-submit keeps the stored one,
    so re-saving the form doesn't wipe a working key. The key is validated by loading it —
    a malformed PEM is rejected here rather than failing later at mint time.
    """
    app_id = str(fields.get("app_id") or "").strip()
    if not app_id:
        secrets.put(BYO_GITHUB_PROFILE, {})
        _token_cache.clear()
        return {"ok": True, "configured": False}
    existing = byo_github_config(secrets)
    key = str(fields.get("private_key") or "").strip() or str(
        existing.get("private_key") or ""
    )
    if not key:
        return {"ok": False, "error": t("error.privateKeyRequired")}
    try:
        _load_key(key)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        # The three `load_pem_private_key` documents: ValueError for a structure it can't
        # decode, TypeError for a password mismatch, UnsupportedAlgorithm for a key type
        # this OpenSSL build lacks. Anything else is a real fault and should surface, not
        # be reported to the user as "bad paste".
        return {"ok": False, "error": t("error.privateKeyUnusable")}
    secrets.put(BYO_GITHUB_PROFILE, {"app_id": app_id, "private_key": key})
    _token_cache.clear()
    return {"ok": True, "configured": True}


def _load_key(pem: str):
    """Parse a PEM private key. GitHub issues PKCS#1 ("BEGIN RSA PRIVATE KEY"); PKCS#8 is
    accepted too since some key managers re-wrap it."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    return load_pem_private_key(pem.encode(), password=None)


# Statuses worth a second attempt: GitHub's rate limiter and its transient 5xx. A 401/404
# is a real answer (bad key, revoked installation) and retrying only delays the failure.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_ATTEMPTS = 3
_BACKOFF = 0.5


def _github_request(
    method: str, url: str, *, jwt_token: str, params: Optional[dict[str, Any]] = None
) -> Optional[dict[str, Any] | list[Any]]:
    """One authenticated GitHub API call with bounded retries. None on any failure.

    Every caller here treats a failure as "this path isn't available right now" rather than
    an error to surface mid-turn, so transport errors, non-success statuses and unparseable
    bodies all collapse to None.
    """
    import httpx

    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    for attempt in range(_ATTEMPTS):
        try:
            resp = httpx.request(
                method, url, headers=headers, params=params, timeout=20
            )
        except httpx.HTTPError:
            if attempt == _ATTEMPTS - 1:
                return None
            time.sleep(_BACKOFF * (2**attempt))
            continue
        if resp.status_code in _RETRY_STATUS and attempt < _ATTEMPTS - 1:
            time.sleep(_BACKOFF * (2**attempt))
            continue
        if resp.status_code not in (200, 201):
            return None
        try:
            return resp.json()
        except ValueError:
            return None
    return None


def app_jwt(secrets: SecretStore) -> str:
    """A signed App JWT, or "" when no usable BYO App is configured."""
    cfg = byo_github_config(secrets)
    app_id, pem = str(cfg.get("app_id") or ""), str(cfg.get("private_key") or "")
    if not (app_id and pem):
        return ""
    import jwt

    now = int(_now())
    payload = {"iat": now - _JWT_SKEW, "exp": now + _JWT_TTL, "iss": app_id}
    try:
        return jwt.encode(payload, _load_key(pem), algorithm="RS256")
    except Exception:
        return ""


def byo_installation_token(
    secrets: SecretStore, installation_id: str, *, force: bool = False
) -> str:
    """A live installation access token for GitHub API calls, minted with the BYO App key.

    Cached in memory until shortly before expiry; `force` skips the cache for the 401 retry
    path. Returns "" when unavailable (no App configured, revoked installation, network
    failure) — callers treat an empty token as "this path isn't available", never as an error
    worth surfacing mid-turn.
    """
    installation_id = str(installation_id or "").strip()
    if not installation_id:
        return ""
    if not force:
        cached = _token_cache.get(installation_id)
        if cached and cached[1] > _now() + _CACHE_LEEWAY:
            return cached[0]
    jwt_token = app_jwt(secrets)
    if not jwt_token:
        return ""
    body = _github_request(
        "POST",
        f"{_API}/app/installations/{installation_id}/access_tokens",
        jwt_token=jwt_token,
    )
    if not isinstance(body, dict):
        return ""  # failed, or valid JSON that isn't an object
    token = str(body.get("token") or "")
    if not token:
        return ""
    expires = _parse_expiry(body.get("expires_at"))
    _token_cache[installation_id] = (token, expires)
    return token


def _parse_expiry(raw: Any) -> float:
    """GitHub returns an ISO-8601 `expires_at`; fall back to the documented one hour when it
    can't be parsed, so a format change degrades to more frequent minting, not to a token
    treated as valid forever."""
    from datetime import datetime

    text = str(raw or "")
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return _now() + 3600


# Enough pages for any realistic App, while still bounding a server that keeps returning
# full pages. Reaching it is logged by the caller rather than silently truncating.
_MAX_PAGES = 10


def list_byo_installations(secrets: SecretStore) -> list[dict[str, Any]]:
    """Installations of the BYO App, for the connect UI to pick from. [] when unavailable.

    Paginated: a picker that silently showed only the first page would look like the
    missing installation was never made.
    """
    jwt_token = app_jwt(secrets)
    if not jwt_token:
        return []
    items: list[Any] = []
    complete = False
    for page in range(1, _MAX_PAGES + 1):
        batch = _github_request(
            "GET",
            f"{_API}/app/installations",
            jwt_token=jwt_token,
            params={"per_page": 100, "page": page},
        )
        if not isinstance(batch, list):
            # A failed page: return what we have rather than nothing, but say so — an
            # incomplete picker otherwise looks like the missing App was never installed.
            logger.warning(
                "github installations: page %d failed; listing %d found so far",
                page,
                len(items),
            )
            break
        items.extend(batch)
        if len(batch) < 100:
            complete = True
            break
    if not complete and len(items) >= _MAX_PAGES * 100:
        logger.warning(
            "github installations: stopped at the %d-page cap (%d listed); "
            "any beyond that are not shown",
            _MAX_PAGES,
            len(items),
        )
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        account = it.get("account") if isinstance(it.get("account"), dict) else {}
        out.append(
            {
                "installation_id": str(it.get("id") or ""),
                "account": str(account.get("login") or ""),
                "account_type": str(account.get("type") or ""),
            }
        )
    return [i for i in out if i["installation_id"]]


def app_slug(secrets: SecretStore) -> Optional[str]:
    """The App's slug, used to build its public install URL. None when unavailable."""
    jwt_token = app_jwt(secrets)
    if not jwt_token:
        return None
    body = _github_request("GET", f"{_API}/app", jwt_token=jwt_token)
    if not isinstance(body, dict):
        return None
    slug = body.get("slug")
    return str(slug) if slug else None


def install_url(secrets: SecretStore) -> Optional[str]:
    """Where to send the browser to install the BYO App on an account/repos."""
    slug = app_slug(secrets)
    return f"https://github.com/apps/{slug}/installations/new" if slug else None
