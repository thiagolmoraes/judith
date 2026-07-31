"""WhatsApp connector — webhook parsing, sending, and the pieces that guard it.

No network: the mappers are pure, and the HTTP calls are exercised through a stubbed
httpx. What matters here is what gets DROPPED (our own echoes, status broadcasts, media
with no text) and how a self-hosted server's address survives the Sender contract.
"""

from __future__ import annotations

import pytest

from coworker.connectors.whatsapp import (
    WhatsAppAdapter,
    extract_text,
    is_group,
    jid_to_number,
    send_whatsapp,
    webhook_to_event,
)


def _upsert(**over) -> dict:
    key = {"remoteJid": "5511999999999@s.whatsapp.net", "fromMe": False, "id": "MSG1"}
    key.update(over.pop("key", {}))
    data = {"key": key, "pushName": "Ana", "message": {"conversation": "oi"}}
    data.update(over)
    return {"event": "messages.upsert", "data": data}


# -- JIDs ----------------------------------------------------------------------
def test_jid_to_number_strips_every_suffix():
    assert jid_to_number("5511999999999@s.whatsapp.net") == "5511999999999"
    assert jid_to_number("120363@g.us") == "120363"
    assert jid_to_number("55119@lid") == "55119"
    assert jid_to_number("") == ""


def test_group_detection():
    assert is_group("120363@g.us") is True
    assert is_group("5511999999999@s.whatsapp.net") is False


# -- text extraction -----------------------------------------------------------
@pytest.mark.parametrize(
    "message,expected",
    [
        ({"conversation": "plain"}, "plain"),
        ({"extendedTextMessage": {"text": "a reply"}}, "a reply"),
        ({"imageMessage": {"caption": "look at this"}}, "look at this"),
        ({"buttonsResponseMessage": {"selectedDisplayText": "Yes"}}, "Yes"),
        ({"listResponseMessage": {"title": "Option A"}}, "Option A"),
    ],
)
def test_extract_text_across_message_shapes(message, expected):
    """WhatsApp uses a different message type per feature, so the text lives under a
    different key for a reply, a caption, or a button tap."""
    assert extract_text(message) == expected


def test_extract_text_returns_empty_for_unhandled_kinds():
    # A sticker or a caption-less image has nothing to act on; "" makes the caller drop it.
    assert extract_text({"stickerMessage": {"url": "..."}}) == ""
    assert extract_text({"imageMessage": {}}) == ""
    assert extract_text({}) == ""
    assert extract_text(None) == ""  # type: ignore[arg-type]


# -- webhook → event -----------------------------------------------------------
def test_dm_becomes_an_addressed_event():
    event = webhook_to_event(_upsert())
    assert event is not None
    assert event.text == "oi"
    # The same id everywhere: the gateway looks up the allow-list by source.platform,
    # so a mismatch reads the official connector's (empty) settings and drops the message.
    assert event.source.platform == "whatsapp_evolution"
    assert event.source.chat_id == "5511999999999@s.whatsapp.net"
    assert event.source.user_id == "5511999999999"  # bare, for the allow-list
    assert event.source.user_name == "Ana"
    assert event.source.chat_type == "dm"
    # A DM is addressed to us by definition — otherwise the agent would never reply.
    assert event.mentions_me is True


def test_group_message_uses_the_participant_as_sender():
    """In a group the chat is the group; the person is `participant`. Getting this wrong
    would allow-list the whole group under one id."""
    event = webhook_to_event(
        _upsert(key={"remoteJid": "120363@g.us", "participant": "5511888888888@s.whatsapp.net"})
    )
    assert event is not None
    assert event.source.chat_type == "group"
    assert event.source.user_id == "5511888888888"
    assert event.source.chat_id == "120363@g.us"
    # Not addressed by default: a group message is not automatically for us.
    assert event.mentions_me is False


def test_our_own_messages_are_dropped():
    """The reply-loop guard. Evolution echoes what WE send back through the same webhook,
    so without this the agent answers itself forever."""
    assert webhook_to_event(_upsert(key={"fromMe": True})) is None


def test_status_broadcasts_and_textless_messages_are_dropped():
    assert webhook_to_event(_upsert(key={"remoteJid": "status@broadcast"})) is None
    assert webhook_to_event(_upsert(message={"stickerMessage": {}})) is None
    assert webhook_to_event(_upsert(message={})) is None


def test_non_message_events_are_ignored():
    assert webhook_to_event({"event": "connection.update", "data": {}}) is None
    assert webhook_to_event({}) is None
    assert webhook_to_event([]) is None  # type: ignore[arg-type]


def test_batched_data_takes_the_first_entry():
    """Some Evolution versions deliver `data` as a list."""
    body = _upsert()
    body["data"] = [body["data"]]
    event = webhook_to_event(body)
    assert event is not None and event.text == "oi"


# -- sending -------------------------------------------------------------------
class _Resp:
    def __init__(self, status: int, payload: dict) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_send_addresses_people_by_number_and_groups_by_jid(monkeypatch):
    """Evolution wants a bare number for a person but the full JID for a group; sending
    a group the stripped id silently delivers nowhere."""
    seen: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append({"url": url, "json": json, "headers": headers})
        return _Resp(201, {"key": {"id": "WAMID1"}})

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)

    ok = send_whatsapp("http://x:8090/", "KEY", "openworker", "5511999999999@s.whatsapp.net", "hi")
    assert ok.ok and ok.message_id == "WAMID1"
    assert seen[0]["json"]["number"] == "5511999999999"
    assert seen[0]["url"] == "http://x:8090/message/sendText/openworker"
    assert seen[0]["headers"]["apikey"] == "KEY"

    send_whatsapp("http://x:8090", "KEY", "openworker", "120363@g.us", "hi")
    assert seen[1]["json"]["number"] == "120363@g.us"


def test_send_converts_markdown_to_whatsapp_styling(monkeypatch):
    """Model output is GitHub markdown; Evolution delivers text verbatim, so the
    conversion has to happen here — the one choke point every send passes through."""
    seen: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.append({"json": json})
        return _Resp(201, {"key": {"id": "WAMID9"}})

    import httpx

    monkeypatch.setattr(httpx, "post", fake_post)

    send_whatsapp(
        "http://x:8090", "KEY", "openworker",
        "5511999999999@s.whatsapp.net", "### Resumo\n**pronto**",
    )
    assert seen[0]["json"]["text"] == "*Resumo*\n*pronto*"


def test_send_surfaces_the_server_error(monkeypatch):
    import httpx

    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _Resp(400, {"response": {"message": "number not on WhatsApp"}})
    )
    result = send_whatsapp("http://x", "K", "openworker", "5511000000000@s.whatsapp.net", "hi")
    assert result.ok is False
    assert "not on WhatsApp" in (result.error or "")


def test_send_rejects_an_empty_recipient():
    assert send_whatsapp("http://x", "K", "openworker", "", "hi").ok is False


# -- the packed token ----------------------------------------------------------
def test_sender_registry_unpacks_the_three_values(monkeypatch):
    """The Sender contract passes ONE token, but a self-hosted server needs address +
    key + instance. They travel packed; this is the seam where that could rot."""
    import httpx

    from coworker.connectors.senders import DEFAULT_SENDERS

    seen: list[str] = []
    monkeypatch.setattr(
        httpx,
        "post",
        lambda url, **k: (seen.append(url), _Resp(201, {"key": {"id": "X"}}))[1],
    )
    result = DEFAULT_SENDERS["whatsapp_evolution"](
        "http://host:8090|SECRET|myinstance", "5511999999999@s.whatsapp.net", "hi", None
    )
    assert result.ok
    assert seen[0] == "http://host:8090/message/sendText/myinstance"


def test_sender_reports_an_unconfigured_connector():
    from coworker.connectors.senders import DEFAULT_SENDERS

    assert DEFAULT_SENDERS["whatsapp_evolution"]("", "5511999@s.whatsapp.net", "hi", None).ok is False


def test_resolve_token_packs_from_the_profile(tmp_path):
    from coworker.connectors.tools import _resolve_token
    from coworker.secrets import SecretStore

    store = SecretStore(tmp_path / "secrets.json")
    store.put(
        "whatsapp_evolution:default",
        {"base_url": "http://localhost:8090", "api_key": "K", "instance": "openworker"},
    )
    assert _resolve_token(store, "whatsapp_evolution", "x") == "http://localhost:8090|K|openworker"
    # No profile → no token, so send_message says "connect it first" rather than 500ing.
    assert _resolve_token(SecretStore(tmp_path / "empty.json"), "whatsapp_evolution", "x") is None


# -- adapter -------------------------------------------------------------------
async def test_connect_refuses_an_unpaired_instance(monkeypatch):
    """A reachable Evolution whose instance is not paired would otherwise register a
    webhook and then fail every send."""

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return _Resp(200, {"instance": {"state": "connecting"}})

        async def post(self, *a, **k):  # pragma: no cover — must not be reached
            raise AssertionError("webhook registered for an unpaired instance")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    adapter = WhatsAppAdapter("http://x", "K", "openworker", webhook_url="http://127.0.0.1:1/webhook/whatsapp")
    assert await adapter.connect() is False


async def test_connect_registers_the_current_webhook_url(monkeypatch):
    """The sidecar's port changes every boot, so the URL is re-registered on connect —
    a URL stored from a previous run points at nothing."""
    posted: list[dict] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return _Resp(200, {"instance": {"state": "open"}})

        async def post(self, url, headers=None, json=None):
            posted.append({"url": url, "json": json})
            return _Resp(200, {"ok": True})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    adapter = WhatsAppAdapter(
        "http://x", "K", "openworker", webhook_url="http://127.0.0.1:54321/webhook/whatsapp"
    )
    assert await adapter.connect() is True
    assert posted[0]["url"] == "http://x/webhook/set/openworker"
    assert posted[0]["json"]["webhook"]["url"] == "http://127.0.0.1:54321/webhook/whatsapp"
    assert posted[0]["json"]["webhook"]["events"] == ["MESSAGES_UPSERT"]


async def test_disconnect_disables_the_webhook_without_logging_out(monkeypatch):
    """Pairing costs a QR scan on the phone. Stopping the connector must not force one."""
    posted: list[dict] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            posted.append({"url": url, "json": json})
            return _Resp(200, {"ok": True})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    adapter = WhatsAppAdapter("http://x", "K", "openworker", webhook_url="http://127.0.0.1:1/w")
    await adapter.disconnect()
    assert posted[0]["json"]["webhook"]["enabled"] is False
    assert all("logout" not in p["url"] for p in posted)


# -- the experimental gate -----------------------------------------------------
def test_connector_is_experimental_and_states_the_risk():
    """Both halves matter. `experimental` hides it until the user opts in; the risk
    notice is what they are shown before connecting, and connect_connector REFUSES
    without an acknowledgement. A descriptor with the flag but no notice would gate on
    an empty string — technically hidden, but the user accepts nothing."""
    from coworker.connectors.descriptors import get_descriptor

    d = get_descriptor("whatsapp_evolution")
    assert d is not None
    assert d.experimental is True
    assert d.risk_notice, "an experimental connector with no risk notice gates on nothing"
    lowered = d.risk_notice.lower()
    assert "ban" in lowered  # the concrete consequence, not a vague warning
    assert "spare" in lowered


def test_connect_refuses_without_acknowledgement(tmp_path, monkeypatch):
    from coworker.connectors import setup as setup_mod
    from coworker.secrets import SecretStore

    store = SecretStore(tmp_path / "secrets.json")
    monkeypatch.setattr(setup_mod, "experimental_enabled", lambda _s: True)
    result = setup_mod.connect_connector(
        store,
        "whatsapp_evolution",
        {"base_url": "http://localhost:8090", "api_key": "K"},
        validate=False,
    )
    assert result["ok"] is False
    # The notice travels WITH the refusal, so the GUI has something to show.
    assert result["risk_notice"]


def test_official_and_self_hosted_whatsapp_coexist():
    """There are two WhatsApp connectors on purpose: Meta's official Cloud API
    (outbound, business number) and this one (two-way, personal number). Collapsing
    them would silently change which API a connected user is on."""
    from coworker.connectors.descriptors import DESCRIPTORS

    official = next(d for d in DESCRIPTORS if d.name == "whatsapp")
    self_hosted = next(d for d in DESCRIPTORS if d.name == "whatsapp_evolution")
    # Split, so a failure names WHICH property drifted.
    assert official.two_way is False
    assert official.experimental is False
    assert self_hosted.two_way is True
    assert self_hosted.experimental is True


# -- gateway wiring ------------------------------------------------------------
def test_platform_is_registered_for_the_gateway():
    """PLATFORMS is what the gateway iterates and what decides whether connecting the
    connector restarts the listeners. Omitting it leaves a connector that saves fine,
    reports connected, and never receives a single message — which is exactly what
    happened before this line existed."""
    from coworker.connectors.config import PLATFORMS

    assert "whatsapp_evolution" in PLATFORMS


def test_enablement_keys_on_the_server_address_not_a_token(tmp_path):
    """Every other platform enables on `bot_token`. A self-hosted server has none — its
    address is the thing that proves it is configured."""
    from coworker.connectors.config import load_settings
    from coworker.secrets import SecretStore

    store = SecretStore(tmp_path / "secrets.json")
    assert load_settings(store)["whatsapp_evolution"].enabled is False

    store.put(
        "whatsapp_evolution:default",
        {"base_url": "http://localhost:8090", "api_key": "K", "allowed_users": ["5511999"]},
    )
    settings = load_settings(store)["whatsapp_evolution"]
    assert settings.enabled is True
    assert settings.allowed_users == {"5511999"}


def test_make_adapter_builds_from_the_profile():
    from coworker.connectors.adapters import make_adapter

    adapter = make_adapter(
        "whatsapp_evolution",
        {"base_url": "http://localhost:8090", "api_key": "K", "instance": "openworker"},
        webhook_url="http://127.0.0.1:5000/webhook/whatsapp",
    )
    assert adapter is not None
    assert adapter.platform == "whatsapp_evolution"
    assert adapter.webhook_url == "http://127.0.0.1:5000/webhook/whatsapp"
    # An unconfigured profile yields nothing rather than a half-built adapter.
    assert make_adapter("whatsapp_evolution", {}) is None


# -- foreign payloads ----------------------------------------------------------
# Evolution is self-hosted: the user may run a different version, or a fork. Anything
# that reaches .get() on a non-dict raises INSIDE a webhook handler, turning a foreign
# payload into a 500 — so every field read from the wire is shape-checked.
@pytest.mark.parametrize(
    "data",
    [
        {"key": ["not", "a", "dict"], "message": {"conversation": "hi"}},
        {"key": "MSG1", "message": {"conversation": "hi"}},
        {"key": {"remoteJid": "5511@s.whatsapp.net"}, "message": "just a string"},
        {"key": {"remoteJid": "5511@s.whatsapp.net"}, "message": ["a", "list"]},
        {"key": None, "message": None},
    ],
)
def test_webhook_survives_unexpected_shapes(data):
    assert webhook_to_event({"event": "messages.upsert", "data": data}) is None


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"instance": {"state": "open"}}, "open"),
        ({"state": "open"}, "open"),  # flat, as some versions answer
        ({"instance": "open"}, ""),  # a string where a dict was expected
        ({"instance": None}, ""),
        (["open"], ""),
        ("open", ""),
        ({}, ""),
    ],
)
def test_connection_state_reads_every_shape(body, expected):
    """An unknown shape must read as "not connected", never raise: this runs inside
    validate(), which is expected to RETURN a failure, not throw one."""
    from coworker.connectors.whatsapp import _connection_state

    assert _connection_state(body) == expected


def test_validate_reports_a_foreign_response_instead_of_raising(monkeypatch):
    """A server answering valid JSON in another shape used to raise AttributeError out
    of validate(), past the error handling the rest of the function does."""
    import httpx

    from coworker.connectors.descriptors import get_descriptor

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(200, ["unexpected"]))
    result = get_descriptor("whatsapp_evolution").validate(
        {"base_url": "http://x", "api_key": "K", "instance": "openworker"}
    )
    assert result.ok is False
    assert "not connected" in (result.error or "")


def test_send_survives_a_non_dict_response(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(201, ["ok"]))
    result = send_whatsapp("http://x", "K", "openworker", "5511@s.whatsapp.net", "hi")
    assert result.ok is True and result.message_id == ""

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(500, "internal error"))
    failed = send_whatsapp("http://x", "K", "openworker", "5511@s.whatsapp.net", "hi")
    assert failed.ok is False and "internal error" in (failed.error or "")


# -- the webhook URL -----------------------------------------------------------
def test_webhook_url_does_not_point_at_loopback(monkeypatch, tmp_path):
    """Evolution normally runs in Docker, where 127.0.0.1 is the CONTAINER. A webhook
    aimed at loopback dies with ECONNREFUSED and the connector reports connected while
    receiving nothing — which is exactly what happened on the first live test."""
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_PORT", "51234")
    monkeypatch.delenv("COWORKER_WEBHOOK_HOST", raising=False)
    url = SessionManager(workspace=tmp_path)._local_webhook_url("whatsapp_evolution")
    assert url == "http://host.docker.internal:51234/webhook/whatsapp"
    assert "127.0.0.1" not in url and "localhost" not in url

    # Evolution on another machine: the host is overridable.
    monkeypatch.setenv("COWORKER_WEBHOOK_HOST", "192.168.1.50")
    assert SessionManager(workspace=tmp_path)._local_webhook_url(
        "whatsapp_evolution"
    ) == "http://192.168.1.50:51234/webhook/whatsapp"


def test_no_webhook_url_for_other_platforms(monkeypatch, tmp_path):
    from coworker.server.manager import SessionManager

    monkeypatch.setenv("COWORKER_PORT", "51234")
    assert SessionManager(workspace=tmp_path)._local_webhook_url("slack") == ""


# -- duplicate suppression -----------------------------------------------------
def test_an_identical_resend_is_suppressed(tmp_path):
    """Observed live: five identical WhatsApp messages in one second, from a model that
    had just been told to send exactly one. Small models re-call a tool after seeing it
    succeed; the person on the other end gets spammed. No prompt wording fixed it, so
    the tool enforces it."""
    from coworker.connectors.base import SendResult
    from coworker.connectors.tools import make_send_message_tool
    from coworker.secrets import SecretStore

    sent: list[str] = []

    def fake_sender(token, chat_id, text, thread_id):
        sent.append(text)
        return SendResult(True, message_id=f"M{len(sent)}")

    store = SecretStore(tmp_path / "secrets.json")
    store.put("telegram:default", {"bot_token": "T"})
    tool = make_send_message_tool(store, senders={"telegram": fake_sender})

    first = tool("telegram:123", "hello")
    assert first["ok"] is True and first.get("duplicate") is None

    second = tool("telegram:123", "hello")
    # Reported as ok, NOT as an error: the message the model wanted delivered has been
    # delivered, and calling it a failure would invite the retry this prevents.
    assert second["ok"] is True
    assert second["duplicate"] is True
    assert sent == ["hello"], "the second call must not reach the network"


def test_a_different_message_still_goes_through(tmp_path):
    """The window suppresses repeats, not conversation."""
    from coworker.connectors.base import SendResult
    from coworker.connectors.tools import make_send_message_tool
    from coworker.secrets import SecretStore

    sent: list[tuple[str, str]] = []

    def fake_sender(token, chat_id, text, thread_id):
        sent.append((chat_id, text))
        return SendResult(True, message_id="M")

    store = SecretStore(tmp_path / "secrets.json")
    store.put("telegram:default", {"bot_token": "T"})
    tool = make_send_message_tool(store, senders={"telegram": fake_sender})

    tool("telegram:123", "hello")
    tool("telegram:123", "a different answer")  # same target, new text
    tool("telegram:999", "hello")  # same text, different person
    assert len(sent) == 3


def test_the_window_expires(tmp_path, monkeypatch):
    """A person who genuinely repeats themselves ("ping" … later "ping") must still get
    through — this is a loop-breaker, not a permanent block."""
    from coworker.connectors import tools as tools_mod
    from coworker.connectors.base import SendResult
    from coworker.secrets import SecretStore

    sent: list[str] = []

    def fake_sender(token, chat_id, text, thread_id):
        sent.append(text)
        return SendResult(True, message_id="M")

    store = SecretStore(tmp_path / "secrets.json")
    store.put("telegram:default", {"bot_token": "T"})
    tool = tools_mod.make_send_message_tool(store, senders={"telegram": fake_sender})

    tool("telegram:123", "ping")
    clock = [0.0]
    monkeypatch.setattr(
        tools_mod, "_DUPLICATE_WINDOW_SECONDS", 30.0, raising=False
    )
    import time

    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)  # an hour later
    tool("telegram:123", "ping")
    assert sent == ["ping", "ping"]


def test_the_duplicate_cache_is_bounded_under_a_burst(tmp_path):
    """Pruning only EXPIRED entries is not a bound: a session sending more than the cap
    inside one window prunes nothing and grows forever. Oldest-first eviction is what
    makes the cap real."""
    from coworker.connectors import tools as tools_mod
    from coworker.connectors.base import SendResult
    from coworker.secrets import SecretStore

    store = SecretStore(tmp_path / "secrets.json")
    store.put("telegram:default", {"bot_token": "T"})
    tool = tools_mod.make_send_message_tool(
        store, senders={"telegram": lambda *a: SendResult(True, message_id="M")}
    )

    # Every message distinct and all inside one window, so nothing can expire.
    for i in range(tools_mod._DUPLICATE_CACHE_MAX * 3):
        tool("telegram:123", f"message {i}")

    # The newest entry still suppresses (the cap keeps recent ones)...
    last = tools_mod._DUPLICATE_CACHE_MAX * 3 - 1
    assert tool("telegram:123", f"message {last}")["duplicate"] is True
    # ...and the very first was evicted, so it sends again rather than being remembered
    # forever.
    assert tool("telegram:123", "message 0").get("duplicate") is None
# -- stale webhook -------------------------------------------------------------
async def test_a_moved_webhook_disables_the_old_url_first(monkeypatch):
    """Evolution retries a failing webhook ten times with backoff, and those retries
    queue AHEAD of live traffic. After a few restarts on new ports, real messages
    arrive minutes late or not at all — while the connector reports healthy. Cost an
    evening of debugging; the fix is disabling the previous URL before registering."""
    posted: list[dict] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            if "connectionState" in url:
                return _Resp(200, {"instance": {"state": "open"}})
            return _Resp(200, {"url": "http://host.docker.internal:1111/webhook/whatsapp"})

        async def post(self, url, headers=None, json=None):
            posted.append(json)
            return _Resp(200, {"ok": True})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    adapter = WhatsAppAdapter(
        "http://x", "K", "openworker", webhook_url="http://host.docker.internal:2222/webhook/whatsapp"
    )
    assert await adapter.connect() is True

    # Old one disabled first, then the new one registered — order matters.
    assert posted[0]["webhook"]["enabled"] is False
    assert posted[0]["webhook"]["url"].endswith(":1111/webhook/whatsapp")
    assert posted[1]["webhook"]["enabled"] is True
    assert posted[1]["webhook"]["url"].endswith(":2222/webhook/whatsapp")


async def test_an_unchanged_webhook_is_not_disabled(monkeypatch):
    """The common case — a restart on the same port. Disabling and re-enabling would
    open a window where inbound messages are dropped."""
    posted: list[dict] = []
    url = "http://host.docker.internal:2222/webhook/whatsapp"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, u, headers=None):
            if "connectionState" in u:
                return _Resp(200, {"instance": {"state": "open"}})
            return _Resp(200, {"url": url})

        async def post(self, u, headers=None, json=None):
            posted.append(json)
            return _Resp(200, {"ok": True})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    assert await WhatsAppAdapter("http://x", "K", "openworker", webhook_url=url).connect() is True
    assert len(posted) == 1
    assert posted[0]["webhook"]["enabled"] is True


async def test_a_failing_lookup_does_not_block_the_connect(monkeypatch):
    """Clearing the old webhook is a nicety; failing to do it must not stop the
    connector from coming up."""
    posted: list[dict] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, u, headers=None):
            if "connectionState" in u:
                return _Resp(200, {"instance": {"state": "open"}})
            raise RuntimeError("find endpoint unavailable on this version")

        async def post(self, u, headers=None, json=None):
            posted.append(json)
            return _Resp(200, {"ok": True})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    adapter = WhatsAppAdapter("http://x", "K", "openworker", webhook_url="http://h:3333/w")
    assert await adapter.connect() is True
    assert posted[0]["webhook"]["enabled"] is True


def test_the_log_line_carries_the_port_not_the_url():
    """A configured webhook URL can carry a token or signature in its query string; an
    info-level log of the whole URL would persist that secret."""
    from coworker.connectors.whatsapp import _url_port

    assert _url_port("http://host.docker.internal:64932/webhook/whatsapp?token=SECRET") == "64932"
    assert _url_port("http://x/webhook") == "?"
    assert _url_port("not a url at all") == "?"


async def test_a_failed_cleanup_is_reported_not_swallowed(monkeypatch, caplog):
    """httpx does not raise on 4xx/5xx. Without checking the status, the cleanup could
    no-op silently and leave the old webhook retrying — the very failure this prevents."""
    import logging

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            if "connectionState" in url:
                return _Resp(200, {"instance": {"state": "open"}})
            return _Resp(200, {"url": "http://host:1111/webhook/whatsapp"})

        async def post(self, url, headers=None, json=None):
            # The disable call is refused; the register call succeeds.
            enabled = (json or {}).get("webhook", {}).get("enabled")
            return _Resp(200 if enabled else 403, {"ok": bool(enabled)})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    adapter = WhatsAppAdapter("http://x", "K", "openworker", webhook_url="http://host:2222/w")
    with caplog.at_level(logging.WARNING, logger="coworker.connectors"):
        assert await adapter.connect() is True  # best effort: never blocks the connect
    assert any("could not disable" in r.message for r in caplog.records)


async def test_a_non_string_old_url_is_ignored(monkeypatch):
    """A fork answering `{"url": {...}}` must not reach the disable call with a dict."""
    posted: list[dict] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            if "connectionState" in url:
                return _Resp(200, {"instance": {"state": "open"}})
            return _Resp(200, {"url": {"unexpected": "shape"}})

        async def post(self, url, headers=None, json=None):
            posted.append(json)
            return _Resp(200, {"ok": True})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: _Client())
    adapter = WhatsAppAdapter("http://x", "K", "openworker", webhook_url="http://host:2222/w")
    assert await adapter.connect() is True
    # Only the registration — no disable call built from a dict.
    assert len(posted) == 1
    assert posted[0]["webhook"]["enabled"] is True
