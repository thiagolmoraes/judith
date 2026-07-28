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
def test_tool_result_errors_stay_english(state):
    """Connector tool errors are serialized into tool results for the model, never
    rendered. Translating them would degrade the model's reasoning for no user benefit.

    Asserted by calling the code with pt-BR active rather than by grepping the module for
    an import: a future change could translate these through some other path and a
    source-text check would stay green while the behaviour broke.
    """
    from coworker.connectors.integration_tools import _profile
    from coworker.secrets import SecretStore

    _set_locale(state, "pt-BR")
    i18n.invalidate_locale_cache()
    assert current_locale() == "pt-BR"  # the locale really is active

    profile, err = _profile(SecretStore(state / "secrets.json"), "notion", "token")
    assert profile is None
    # English, in the shape the model consumes — no {"ok": False} envelope either.
    assert err == {"error": "notion is not connected; missing token"}


# -- prefs robustness -----------------------------------------------------------
@pytest.mark.parametrize(
    "content",
    ["[]", '"pt-BR"', "42", "null", "{not json", '{"locale": 7}'],
)
def test_malformed_prefs_fall_back_without_raising(state, content):
    """Valid JSON isn't necessarily an object: `[]`, `"pt-BR"` and `42` all parse, and
    calling .get() on them raises — inside a request handler that is a 500 over a
    cosmetic string."""
    (state / "prefs.json").write_text(content, encoding="utf-8")
    i18n.invalidate_locale_cache()
    assert current_locale() == DEFAULT_LOCALE


def test_undecodable_prefs_fall_back(state):
    """Not valid UTF-8 — UnicodeDecodeError isn't a JSONDecodeError, so a narrower
    except would let it escape."""
    (state / "prefs.json").write_bytes(b"\xff\xfe not utf-8")
    i18n.invalidate_locale_cache()
    assert current_locale() == DEFAULT_LOCALE


# -- the read cache -------------------------------------------------------------
def test_locale_is_not_read_from_disk_on_every_call(state, monkeypatch):
    """`t()` runs inside async route handlers, several times per rendered page. Reading
    prefs.json on each call would block the event loop."""
    import pathlib

    _set_locale(state, "pt-BR")
    i18n.invalidate_locale_cache()

    reads = 0
    real = pathlib.Path.read_text

    def counting(self, *args, **kwargs):
        nonlocal reads
        if self.name == "prefs.json":
            reads += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", counting)
    for _ in range(25):
        assert current_locale() == "pt-BR"
    assert reads == 1


@pytest.mark.parametrize(
    "content", ["{not json", "[]", "42", '"pt-BR"', '{"locale": "kl-GL"}']
)
def test_fallback_paths_are_cached_too(state, monkeypatch, content):
    """The fallbacks were the gap: a corrupt prefs file is exactly when a page would
    re-read it once per t() call — 14 blocking reads to render one callback page — since
    only the happy path populated the cache."""
    import pathlib

    (state / "prefs.json").write_text(content, encoding="utf-8")
    i18n.invalidate_locale_cache()

    reads = 0
    real = pathlib.Path.read_text

    def counting(self, *args, **kwargs):
        nonlocal reads
        if self.name == "prefs.json":
            reads += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", counting)
    for _ in range(14):
        assert current_locale() == DEFAULT_LOCALE
    assert reads == 1


def test_undecodable_prefs_are_cached_too(state, monkeypatch):
    """UnicodeDecodeError raises from read_text itself, so it takes a different path
    through the cache than a JSON error."""
    import pathlib

    (state / "prefs.json").write_bytes(b"\xff\xfe not utf-8")
    i18n.invalidate_locale_cache()

    reads = 0
    real = pathlib.Path.read_text

    def counting(self, *args, **kwargs):
        nonlocal reads
        if self.name == "prefs.json":
            reads += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", counting)
    for _ in range(14):
        assert current_locale() == DEFAULT_LOCALE
    assert reads == 1


def test_cache_notices_a_locale_change(state):
    """Keyed on the file's mtime and size rather than a timeout, so switching language
    applies on the next call with no staleness window."""
    import os
    import time

    _set_locale(state, "pt-BR")
    i18n.invalidate_locale_cache()
    assert current_locale() == "pt-BR"

    _set_locale(state, "en")
    # Bump mtime explicitly: two writes inside one filesystem tick can share a timestamp,
    # which would make this test flaky rather than the cache wrong.
    stat = (state / "prefs.json").stat()
    os.utime(state / "prefs.json", ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    time.sleep(0.001)
    assert current_locale() == "en"


def test_explicit_prefs_bypass_the_cache(state):
    """Passing prefs in is the escape hatch for callers that already hold them."""
    _set_locale(state, "en")
    i18n.invalidate_locale_cache()
    assert current_locale({"locale": "pt-BR"}) == "pt-BR"
    assert current_locale() == "en"  # the file still wins when nothing is passed


# -- automation schedule labels ---------------------------------------------------
def test_schedule_labels_follow_the_locale(state):
    """Schedule.human() renders in the sidebar and the Automations page, so it is
    user-facing despite being built server-side. It was shipping raw English."""
    from coworker.automation.models import Schedule

    _set_locale(state, "pt-BR")
    i18n.invalidate_locale_cache()

    assert Schedule(kind="once", fire_at="2026-07-27T22:52:01-03:00").human() == (
        "Uma vez em 2026-07-27T22:52:01-03:00"
    )
    assert Schedule(kind="cron", cron="10 19 * * *").human() == (
        "Todo dia por volta de 19:10"
    )
    assert Schedule(kind="cron", cron="10 19 15 * *").human() == (
        "Todo mês no dia 15 por volta de 19:10"
    )


def test_schedule_weekday_indexing_matches_cron(state):
    """In cron, day-of-week 0 is SUNDAY. The old list started at Monday, so every
    weekly label named the wrong day — a Sunday automation read "Every Monday"."""
    from coworker.automation.models import Schedule

    _set_locale(state, "en")
    i18n.invalidate_locale_cache()
    assert Schedule(kind="cron", cron="0 9 * * 0").human() == "Every Sunday at ~9:00 AM"
    assert Schedule(kind="cron", cron="0 9 * * 6").human() == (
        "Every Saturday at ~9:00 AM"
    )

    _set_locale(state, "pt-BR")
    i18n.invalidate_locale_cache()
    assert Schedule(kind="cron", cron="0 9 * * 0").human() == (
        "todo domingo por volta de 09:00"
    )


def test_schedule_time_follows_the_locale_convention(state):
    """12-hour AM/PM is an English convention; pt-BR reads 24-hour. This is a
    formatting rule, not a translation, which is why _human_time takes the locale."""
    from coworker.automation.models import Schedule

    _set_locale(state, "en")
    i18n.invalidate_locale_cache()
    assert "7:10 PM" in Schedule(kind="cron", cron="10 19 * * *").human()

    _set_locale(state, "pt-BR")
    i18n.invalidate_locale_cache()
    assert "19:10" in Schedule(kind="cron", cron="10 19 * * *").human()


def test_schedule_weekday_gender_agreement(state):
    """sábado and domingo are masculine, the -feira weekdays feminine — a fixed
    "Toda {day}" frame is wrong two days out of seven, so the article rides with
    the day name."""
    from coworker.automation.models import Schedule

    _set_locale(state, "pt-BR")
    i18n.invalidate_locale_cache()
    assert Schedule(kind="cron", cron="0 9 * * 6").human().startswith("todo sábado")
    assert Schedule(kind="cron", cron="0 9 * * 1").human().startswith(
        "toda segunda-feira"
    )


def test_schedule_falls_back_to_raw_cron_untranslated(state):
    """Ranges and steps have no natural-language frame; the raw cron shows as-is in
    both locales rather than half-translating."""
    from coworker.automation.models import Schedule

    _set_locale(state, "pt-BR")
    i18n.invalidate_locale_cache()
    assert Schedule(kind="cron", cron="*/5 * * * *").human() == "*/5 * * * *"
