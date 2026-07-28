"""Connector catalogue in Portuguese: blurbs, setup steps, field help, and the
detail-page About/Access copy.

The load-bearing test here is coverage: the catalogue is keyed on English source text, so
copy edited in the descriptors falls back to English rather than shipping a stale
translation — safe, but silent. `test_every_catalogue_string_is_translated` is what makes
it not silent.
"""

from __future__ import annotations

import json

import pytest

from coworker.connectors import catalog_copy
from coworker.connectors.catalog_i18n import PT_BR, translate, translate_all
from coworker.connectors.descriptors import DESCRIPTORS
from coworker.connectors.setup import connector_list
from coworker.secrets import SecretStore


def _catalogue_strings() -> list[tuple[str, str]]:
    """(where, text) for every user-visible catalogue string."""
    out: list[tuple[str, str]] = []
    for d in DESCRIPTORS:
        if d.blurb:
            out.append((f"{d.name}.blurb", d.blurb))
        for step in d.instructions or []:
            out.append((f"{d.name}.instructions", step))
        for field in d.fields or []:
            if field.help:
                out.append((f"{d.name}.{field.key}.help", field.help))
        # The risk notice was outside this sweep, which meant the ONE string an
        # experimental connector most needs a user to understand was the one that could
        # ship untranslated.
        if d.risk_notice:
            out.append((f"{d.name}.risk_notice", d.risk_notice))
    for name, text in catalog_copy.ABOUT.items():
        if text:
            out.append((f"about.{name}", text))
    for name, bullets in catalog_copy.ACCESS.items():
        for bullet in bullets:
            out.append((f"access.{name}", bullet))
    return out


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    return tmp_path / "state"


def _connectors(state, locale: str) -> dict[str, dict]:
    from coworker import i18n

    (state / "prefs.json").write_text(json.dumps({"locale": locale}), encoding="utf-8")
    i18n.invalidate_locale_cache()
    return {c["name"]: c for c in connector_list(SecretStore(state / "secrets.json"))}


# -- coverage -------------------------------------------------------------------
def test_every_catalogue_string_is_translated():
    """Keyed on source text, so an edited descriptor silently reverts to English. This is
    the test that surfaces it — the failure message names what to add."""
    missing = [
        (where, text) for where, text in _catalogue_strings() if text not in PT_BR
    ]
    assert not missing, "untranslated catalogue copy:\n" + "\n".join(
        f"  {where}: {text}" for where, text in missing
    )


def test_no_stale_entries():
    """A translation whose English key no longer exists is dead weight, and usually means
    the copy was reworded — in which case the *new* wording needs translating."""
    live = {text for _, text in _catalogue_strings()}
    stale = sorted(key for key in PT_BR if key not in live)
    assert not stale, "translations with no matching source string:\n" + "\n".join(
        f"  {key}" for key in stale
    )


def test_prose_entries_are_actually_translated():
    """Guards against a key pasted in without being translated.

    Pure menu paths ("Figma → Settings → Security → Personal access tokens.") are
    deliberately identical: they name items the user is about to read in the vendor's own
    English UI. Those are excluded by shape — an entry that is only arrows and proper
    nouns — so the check still bites on real prose.
    """

    def is_menu_path(text: str) -> bool:
        return "→" in text and len(text.replace("→", " ").split()) <= 10

    identical = [
        k
        for k, v in PT_BR.items()
        if k == v and len(k.split()) > 4 and not is_menu_path(k)
    ]
    assert not identical, f"untranslated entries: {identical}"


# -- what is deliberately left alone --------------------------------------------
def test_product_names_are_untouched(state):
    """Titles are brands. Translating "Browser" would leave the card disagreeing with its
    icon, the search box, and the vendor's own site."""
    en = _connectors(state, "en")
    pt = _connectors(state, "pt-BR")
    for name in ("slack", "github", "monday", "browser", "email"):
        if name in en:
            assert pt[name]["title"] == en[name]["title"]


def test_field_labels_are_untouched(state):
    """A label names the value to paste — "Bot token", "App token" — which the user is
    copying out of the vendor's own English UI, so it must read identically in both."""
    en = _connectors(state, "en")
    pt = _connectors(state, "pt-BR")
    for name in ("slack", "github", "gitlab"):
        en_labels = [f["label"] for f in en[name]["fields"]]
        pt_labels = [f["label"] for f in pt[name]["fields"]]
        assert en_labels == pt_labels, name


def test_identifiers_survive_translation():
    """Scopes, token prefixes and hostnames must appear verbatim in the Portuguese, or the
    instructions stop working."""
    step = translate(
        "Install to workspace and copy the Bot User OAuth Token (xoxb-).", "pt-BR"
    )
    assert "xoxb-" in step
    scopes = translate(
        "Create a GitLab personal access token with the read_api scope (api for write actions).",
        "pt-BR",
    )
    assert "read_api" in scopes


# -- behaviour ------------------------------------------------------------------
def test_english_locale_passes_text_through():
    text = "Search, summarize, draft, and send email."
    assert translate(text, "en") == text


def test_unknown_string_falls_back_to_itself():
    """Copy edited since this catalogue was written ships in English rather than as a key
    or a stale translation."""
    assert translate("A blurb nobody has translated yet.", "pt-BR") == (
        "A blurb nobody has translated yet."
    )


def test_empty_and_missing_text_are_safe():
    assert translate("", "pt-BR") == ""
    assert translate_all([], "pt-BR") == []


def test_connector_payload_is_translated(state):
    pt = _connectors(state, "pt-BR")
    github = pt["github"]
    assert github["blurb"].startswith("Trabalhe com issues")
    assert "Lê código" in github["access"][0]
    assert pt["telegram"]["instructions"][0].startswith("Abra o Telegram")


def test_english_payload_is_unchanged(state):
    """The default must be byte-identical to what shipped before translation existed."""
    en = _connectors(state, "en")
    assert en["github"]["blurb"] == (
        "Work with issues, pull requests, repository files, and CI status."
    )
    assert en["telegram"]["instructions"][0] == "Open Telegram and message @BotFather."


def test_field_help_is_translated_but_keys_are_not(state):
    pt = _connectors(state, "pt-BR")
    gitlab = {f["key"]: f for f in pt["gitlab"]["fields"]}
    assert "base_url" in gitlab  # the key is an identifier, not copy
    assert gitlab["base_url"]["help"] == "Deixe vazio para gitlab.com."
