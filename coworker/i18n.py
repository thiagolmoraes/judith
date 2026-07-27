"""Backend translation, for the strings that actually reach a person.

Deliberately narrow. Most ``{"ok": False, "error": ...}`` returns in this codebase are
tool results consumed by the model, not text rendered on screen — translating those would
hurt (the model reasons better in English) and nobody would ever read the Portuguese. So
this covers only two things:

* errors the GUI displays, which arrive as the ``error`` field of a REST response
* the loopback HTML pages a browser lands on after an OAuth flow

The locale is read from the same pref the GUI uses (``locale`` in ``prefs.json``), so one
choice drives both surfaces. English is the source language and the fallback; an untranslated
key returns its English text rather than the key, because unlike the GUI these strings can
end up in a log or an API response where a bare key is worse than an English sentence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

DEFAULT_LOCALE = "en"
LOCALES = ("en", "pt-BR")

# key -> {locale: text}. English is required; a locale missing a key falls back to it.
#
# Keys are grouped by surface. Only add one here when the string is genuinely user-visible:
# the value of this file is that everything in it is known to render somewhere.
_MESSAGES: dict[str, dict[str, str]] = {
    # -- loopback browser pages (server/app.py) ---------------------------------
    "page.signedIn.title": {"en": "Signed in", "pt-BR": "Login concluído"},
    "page.signedIn.detail": {
        "en": "You're signed in to OpenWorker Cloud. You can close this tab and return to OpenWorker.",
        "pt-BR": "Login no OpenWorker Cloud concluído. Você pode fechar esta aba e voltar para o OpenWorker.",
    },
    "page.signInFailed.title": {"en": "Sign-in failed", "pt-BR": "Falha no login"},
    "page.signInFailed.detail": {
        "en": "Close this tab and try signing in again from OpenWorker.",
        "pt-BR": "Feche esta aba e tente entrar de novo pelo OpenWorker.",
    },
    "page.connected.title": {"en": "Connected", "pt-BR": "Conectado"},
    "page.connectorConnected.title": {
        "en": "{connector} connected",
        "pt-BR": "{connector} conectado",
    },
    "page.connected.detail": {
        "en": "You can close this tab and return to OpenWorker.",
        "pt-BR": "Você pode fechar esta aba e voltar para o OpenWorker.",
    },
    "page.connectFailed.title": {
        "en": "Connection failed",
        "pt-BR": "Falha na conexão",
    },
    "page.connectFailed.detail": {
        "en": (
            "Something went wrong finishing this connection. "
            "Close this tab and try again from OpenWorker."
        ),
        "pt-BR": (
            "Algo deu errado ao concluir esta conexão. "
            "Feche esta aba e tente de novo pelo OpenWorker."
        ),
    },
    "page.serviceError.detail": {
        "en": "The service reported an error. Return to OpenWorker and try again.",
        "pt-BR": "O serviço retornou um erro. Volte ao OpenWorker e tente de novo.",
    },
    "page.timedOut.detail": {
        "en": "The sign-in may have timed out. Return to OpenWorker and start it again.",
        "pt-BR": "O login pode ter expirado. Volte ao OpenWorker e comece de novo.",
    },
    "page.signInComplete.detail": {
        "en": "Sign-in complete. You can close this tab and return to OpenWorker.",
        "pt-BR": "Login concluído. Você pode fechar esta aba e voltar para o OpenWorker.",
    },
    "page.footer": {
        "en": "Served locally by OpenWorker on your Mac",
        "pt-BR": "Servido localmente pelo OpenWorker no seu Mac",
    },
    "page.nothingWaiting.title": {
        "en": "Nothing waiting for this sign-in",
        "pt-BR": "Nenhum login aguardando",
    },
    "page.nothingWaiting.detail": {
        "en": "You can close this tab and return to OpenWorker.",
        "pt-BR": "Você pode fechar esta aba e voltar para o OpenWorker.",
    },
    # -- OS-level prompts -------------------------------------------------------
    # Rendered by the platform's own folder picker (osascript on macOS, a WinForms
    # dialog on Windows), so it must contain no quotes: it is interpolated into a
    # shell-quoted AppleScript string and a PowerShell single-quoted literal.
    "picker.folderPrompt": {
        "en": "Give the coworker access to a folder",
        "pt-BR": "Dê ao coworker acesso a uma pasta",
    },
    # -- errors the GUI renders -------------------------------------------------
    "error.noWorkspace": {
        "en": "no valid workspace — choose a project folder first",
        "pt-BR": "nenhuma pasta de trabalho válida — escolha uma pasta de projeto primeiro",
    },
    "error.unknownConnector": {
        "en": "unknown or unavailable connector",
        "pt-BR": "conector desconhecido ou indisponível",
    },
    "error.experimentalDisabled": {
        "en": "experimental connectors are disabled",
        "pt-BR": "conectores experimentais estão desativados",
    },
    "error.riskAcknowledgement": {
        "en": "risk acknowledgment required",
        "pt-BR": "é necessário aceitar o aviso de risco",
    },
    "error.missingFields": {
        "en": "missing: {fields}",
        "pt-BR": "faltando: {fields}",
    },
    "error.notSignedIn": {"en": "not signed in", "pt-BR": "sem login"},
    "error.cloudDisabled": {
        "en": "OpenWorker Cloud is disabled",
        "pt-BR": "o OpenWorker Cloud está desativado",
    },
    "error.unknownProvider": {
        "en": "unknown provider: {name}",
        "pt-BR": "provedor desconhecido: {name}",
    },
    "error.unknownLocale": {
        "en": "unknown locale: {value}",
        "pt-BR": "idioma desconhecido: {value}",
    },
    "error.emptyModel": {"en": "empty model", "pt-BR": "modelo vazio"},
    "error.fieldsMustBeObject": {
        "en": "fields must be an object",
        "pt-BR": "fields precisa ser um objeto",
    },
    "error.noByoPath": {
        "en": "{connector} has no BYO OAuth path",
        "pt-BR": "{connector} não tem caminho para app OAuth próprio",
    },
    "error.clientSecretRequired": {
        "en": "client_secret required",
        "pt-BR": "client_secret é obrigatório",
    },
    "error.privateKeyRequired": {
        "en": "private_key required",
        "pt-BR": "private_key é obrigatória",
    },
    "error.privateKeyUnusable": {
        "en": "private key not usable: {reason}",
        "pt-BR": "chave privada inutilizável: {reason}",
    },
    "error.scopesShape": {
        "en": "scopes must be a list or a string",
        "pt-BR": "scopes precisa ser uma lista ou um texto",
    },
    "error.noByoAppConfigured": {
        "en": "no BYO app configured for {connector}",
        "pt-BR": "nenhum app próprio configurado para {connector}",
    },
    "error.noByoGithubApp": {
        "en": "no BYO GitHub App configured",
        "pt-BR": "nenhum GitHub App próprio configurado",
    },
    "error.tokenExchangeFailed": {
        "en": "token exchange failed",
        "pt-BR": "a troca de token falhou",
    },
    "error.noAccessToken": {
        "en": "provider returned no access token",
        "pt-BR": "o provedor não retornou um access token",
    },
    "error.unknownConnectionAttempt": {
        "en": "unknown or expired connection attempt",
        "pt-BR": "tentativa de conexão desconhecida ou expirada",
    },
}


def _state_dir() -> Path:
    from .secrets import state_dir

    return state_dir()


def current_locale(prefs: Optional[dict[str, Any]] = None) -> str:
    """The interface language, from the same pref the GUI writes.

    Reading the file rather than threading a locale through every call site: these strings
    are produced deep inside request handlers, and a parameter on each would be a far
    larger change than the translation itself. Unreadable or unknown → English.
    """
    if prefs is None:
        try:
            prefs = json.loads(
                (_state_dir() / "prefs.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            return DEFAULT_LOCALE
    value = str((prefs or {}).get("locale") or "").strip()
    return value if value in LOCALES else DEFAULT_LOCALE


def t(key: str, /, locale: Optional[str] = None, **params: Any) -> str:
    """Translate `key`, formatting `{placeholders}` from `params`.

    Falls back to English, then to the key. Unlike the GUI, a missing key returns the key
    itself only as a last resort — these strings can land in a log or an API response,
    where an English sentence is more useful than a bare identifier.
    """
    entry = _MESSAGES.get(key)
    if entry is None:
        return key
    text = entry.get(locale or current_locale()) or entry.get(DEFAULT_LOCALE) or key
    if not params:
        return text
    try:
        return text.format(**params)
    except (KeyError, IndexError):
        # A placeholder without a value: return the unformatted text rather than raising
        # inside a request handler over a cosmetic string.
        return text
