"""Backend translation: the message table, and the surfaces that render it.

The scope is deliberate — only strings a person actually sees are translated. Tool-result
errors stay English because they're consumed by the model, and a test here pins that so
the boundary doesn't drift.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from coworker import i18n
from coworker.i18n import DEFAULT_LOCALE, LOCALES, current_locale, t
from coworker.server import SessionManager, create_app


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    return tmp_path / "state"


def _set_locale(state, value: str) -> None:
    (state / "prefs.json").write_text(json.dumps({"locale": value}), encoding="utf-8")


# -- the table ------------------------------------------------------------------
def test_every_key_has_english_and_portuguese():
    """English is the fallback, so a key without it can only render as itself. A key
    without pt-BR silently stays English, which is the bug this catches early."""
    for key, entry in i18n._MESSAGES.items():
        assert entry.get("en"), f"{key} has no English source"
        assert entry.get("pt-BR"), f"{key} is not translated"


def test_placeholders_match_across_locales():
    """A translation that drops or renames a placeholder renders a literal `{name}` on
    screen, or silently loses the value."""
    import re

    for key, entry in i18n._MESSAGES.items():
        names = {
            locale: set(re.findall(r"\{(\w+)\}", text))
            for locale, text in entry.items()
        }
        assert len(set(map(frozenset, names.values()))) == 1, (
            f"{key} has mismatched placeholders: {names}"
        )


def test_translates_and_interpolates(state):
    assert t("error.notSignedIn", locale="en") == "not signed in"
    assert t("error.notSignedIn", locale="pt-BR") == "sem login"
    assert t("error.noByoPath", locale="pt-BR", connector="datadog").startswith(
        "datadog"
    )


def test_unknown_key_returns_itself(state):
    assert t("no.such.key") == "no.such.key"


def test_missing_placeholder_does_not_raise(state):
    """These are cosmetic strings produced inside request handlers — a formatting slip
    must not turn into a 500."""
    assert "{connector}" in t("error.noByoPath", locale="pt-BR")


# -- locale resolution ----------------------------------------------------------
def test_locale_defaults_to_english_without_prefs(state):
    assert current_locale() == DEFAULT_LOCALE


def test_locale_follows_the_gui_preference(state):
    _set_locale(state, "pt-BR")
    assert current_locale() == "pt-BR"
    assert t("error.notSignedIn") == "sem login"


def test_unknown_or_corrupt_prefs_fall_back(state):
    _set_locale(state, "kl-GL")
    assert current_locale() == DEFAULT_LOCALE
    (state / "prefs.json").write_text("{not json", encoding="utf-8")
    assert current_locale() == DEFAULT_LOCALE


def test_locales_match_the_manager(state):
    """The GUI writes this pref; the two lists disagreeing would let a locale be selected
    that the backend then ignores."""
    assert set(LOCALES) == set(SessionManager.LOCALES)


# -- the loopback pages ---------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    manager = SessionManager(workspace=tmp_path)
    with TestClient(create_app(manager)) as c:
        c.state_dir = tmp_path / "state"
        yield c


def test_callback_page_is_english_by_default(client):
    body = client.get("/oauth/callback?error=denied").text
    assert "Connection failed" in body
    assert "Something went wrong finishing this connection" in body


def test_callback_page_follows_the_locale(client):
    _set_locale(client.state_dir, "pt-BR")
    body = client.get("/oauth/callback?error=denied").text
    assert "Falha na conexão" in body
    assert "Algo deu errado" in body
    assert "Feche esta aba" in body


def test_page_footer_is_translated(client):
    """It appears on every loopback page, so an untranslated footer would leave every
    one of them half-English."""
    _set_locale(client.state_dir, "pt-BR")
    body = client.get("/oauth/callback?error=denied").text
    assert "Servido localmente" in body
    assert "Served locally" not in body


def test_page_has_no_mixed_language_sentences(client):
    """A sentence assembled from a translated half and a hardcoded English half reads as
    broken rather than untranslated — worse than leaving it in English."""
    _set_locale(client.state_dir, "pt-BR")
    body = client.get("/oauth/callback?error=denied").text
    for english in ("Close this tab", "Return to OpenWorker", "You can close"):
        assert english not in body, f"leftover English fragment: {english}"


# -- error strings the GUI renders ----------------------------------------------
def test_connector_errors_are_translated(client):
    _set_locale(client.state_dir, "pt-BR")
    body = client.post("/v1/connectors/nope/connect", json={"fields": {}}).json()
    assert body["ok"] is False
    assert body["error"] == "conector desconhecido ou indisponível"


def test_byo_errors_are_translated(client):
    _set_locale(client.state_dir, "pt-BR")
    body = client.post("/v1/connectors/datadog/byo-connect").json()
    assert body["ok"] is False
    assert "não tem caminho" in body["error"]


def test_missing_fields_error_keeps_its_values(client):
    """The field names are identifiers, not prose — the sentence around them translates,
    the names stay verbatim so they still match the form."""
    _set_locale(client.state_dir, "pt-BR")
    body = client.post("/v1/connectors/github/connect", json={"fields": {}}).json()
    assert body["ok"] is False
    assert body["error"].startswith("faltando:")


# -- the boundary ---------------------------------------------------------------
def test_tool_result_errors_stay_english():
    """Connector tool errors are serialized into tool results for the model, never
    rendered. Translating them would degrade the model's reasoning for no user benefit,
    so the table must not grow keys for them."""
    from coworker.connectors import integration_tools

    source = open(integration_tools.__file__, encoding="utf-8").read()
    assert "i18n import t" not in source
    assert "from ..i18n" not in source
