"""WhatsApp through a self-hosted Evolution API instance.

Unlike Telegram (official bot API) and Slack (official app), WhatsApp has no first-party
API for a personal number. Evolution API is a self-hosted server that speaks the
unofficial multi-device protocol and exposes it as REST + webhooks; this connector talks
to *that*, never to WhatsApp directly. Swapping Evolution for another server (WAHA, a
whatsmeow wrapper) is a change confined to this file.

Inbound is a webhook: Evolution POSTs to the sidecar. The sidecar's port is assigned at
boot, so the adapter REGISTERS the webhook on connect with the port it is currently
listening on — otherwise every restart would need the URL re-entered by hand.

The operator must understand what this is: automating a personal WhatsApp account
violates Meta's terms and the number can be banned. That belongs in the connector's
copy, not buried here.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .base import BasePlatformAdapter, MessageEvent, SendResult, SessionSource

logger = logging.getLogger("coworker.connectors")

# Evolution addresses chats as JIDs: "5511999999999@s.whatsapp.net" for a person,
# "...@g.us" for a group. We keep the full JID as chat_id (it is what send takes back),
# but need the bare number for display and for the allow-list.
_JID_SUFFIX_RE = re.compile(r"@(s\.whatsapp\.net|g\.us|lid)$")


def jid_to_number(jid: str) -> str:
    """"5511999999999@s.whatsapp.net" → "5511999999999"."""
    return _JID_SUFFIX_RE.sub("", jid or "")


def is_group(jid: str) -> bool:
    return (jid or "").endswith("@g.us")


def _connection_state(body: Any) -> str:
    """The instance's state, from whichever shape the server used.

    Evolution reports it as `{"instance": {"state": …}}`; some versions and forks answer
    a flat `{"state": …}`. Anything else (a list, a string, an error page decoded as
    JSON) yields "" — an unknown state, treated as not-connected, never an exception.
    """
    if not isinstance(body, dict):
        return ""
    instance = body.get("instance")
    if isinstance(instance, dict) and instance.get("state"):
        return str(instance["state"])
    state = body.get("state")
    return str(state) if isinstance(state, str) else ""


def extract_text(message: dict) -> str:
    """The text of a message, across the shapes Evolution forwards.

    WhatsApp has a separate message type per feature, so the text lives under a different
    key for a plain message, a reply, a caption, or an interactive-list pick. Anything we
    don't recognise (image without caption, sticker, audio) yields "" and is dropped by
    the caller — better silence than a half-parsed event.
    """
    if not isinstance(message, dict):
        return ""
    if isinstance(message.get("conversation"), str):
        return message["conversation"]
    for key in ("extendedTextMessage", "imageMessage", "videoMessage", "documentMessage"):
        node = message.get(key)
        if isinstance(node, dict):
            text = node.get("text") or node.get("caption")
            if isinstance(text, str) and text:
                return text
    for key, field in (
        ("buttonsResponseMessage", "selectedDisplayText"),
        ("listResponseMessage", "title"),
        ("templateButtonReplyMessage", "selectedDisplayText"),
    ):
        node = message.get(key)
        if isinstance(node, dict) and isinstance(node.get(field), str):
            return node[field]
    return ""


def webhook_to_event(payload: dict) -> Optional[MessageEvent]:
    """Map one Evolution `messages.upsert` webhook body to a MessageEvent.

    Returns None for anything that isn't an inbound text message: our own echoes
    (`fromMe`), status broadcasts, and media without a caption. Dropping our own
    messages is what stops the agent replying to itself in a loop.
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("event") not in (None, "messages.upsert"):
        return None
    data = payload.get("data")
    if isinstance(data, list):  # some versions batch
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None

    key = data.get("key")
    # A different Evolution version (or a fork) can answer valid JSON in another shape.
    # `key` being a list or a string would make .get() raise inside a webhook handler,
    # which turns a foreign payload into a 500 instead of a quiet drop.
    if not isinstance(key, dict):
        return None
    if key.get("fromMe"):
        return None
    chat_id = str(key.get("remoteJid") or "")
    if not chat_id or chat_id == "status@broadcast":
        return None

    message = data.get("message")
    text = extract_text(message if isinstance(message, dict) else {})
    if not text:
        return None

    group = is_group(chat_id)
    # In a group the sender is `participant`; in a DM it's the chat itself.
    sender_jid = str(key.get("participant") or chat_id)
    source = SessionSource(
        # Same string as the adapter and the descriptor: the gateway looks up the
        # allow-list by this, so "whatsapp" would read the OFFICIAL connector's
        # settings — an empty allow-list, silently dropping every message.
        platform="whatsapp_evolution",
        chat_id=chat_id,
        user_id=jid_to_number(sender_jid),
        user_name=data.get("pushName") or None,
        chat_type="group" if group else "dm",
    )
    return MessageEvent(
        text=text,
        source=source,
        message_id=str(key.get("id") or ""),
        # A DM is addressed to us by definition; in a group only an explicit @mention is.
        mentions_me=not group,
    )


class WhatsAppAdapter(BasePlatformAdapter):
    """Inbound via webhook, outbound via Evolution's REST API.

    `connect()` does not open a socket — it verifies the instance is paired and points
    Evolution's webhook at this sidecar. The FastAPI route hands events back through
    `handle_message`.
    """

    # Must match the descriptor name: the gateway registers adapters by this string and
    # the webhook route looks one up by it. "whatsapp" would collide with the official
    # Cloud API connector and leave inbound messages unroutable.
    platform = "whatsapp_evolution"

    def __init__(self, base_url: str, api_key: str, instance: str, *, webhook_url: str = "") -> None:
        super().__init__()
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.instance = instance or "openworker"
        self.webhook_url = webhook_url

    def _headers(self) -> dict:
        return {"apikey": self.api_key, "Content-Type": "application/json"}

    async def connect(self) -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                state = await client.get(
                    f"{self.base_url}/instance/connectionState/{self.instance}",
                    headers=self._headers(),
                )
                body = state.json() if state.status_code == 200 else {}
                status = _connection_state(body)
                if status != "open":
                    logger.warning(
                        "whatsapp instance %s is %s — pair it in the Evolution manager",
                        self.instance,
                        status or "unreachable",
                    )
                    return False

                if self.webhook_url:
                    # Re-registered every connect: the sidecar's port is assigned at
                    # boot, so a URL stored from a previous run points nowhere.
                    #
                    # When the URL has CHANGED, disable the old one first. Evolution
                    # retries a failing webhook ten times with backoff, and those
                    # retries queue ahead of live traffic — after a few restarts on new
                    # ports, real messages arrive minutes late or not at all, while the
                    # connector looks healthy. Cost me an evening; the fix is one extra
                    # call.
                    try:
                        current = await client.get(
                            f"{self.base_url}/webhook/find/{self.instance}",
                            headers=self._headers(),
                        )
                        old = (current.json() or {}) if current.status_code == 200 else {}
                        old_url = old.get("url") if isinstance(old, dict) else None
                        if old_url and old_url != self.webhook_url:
                            logger.info(
                                "whatsapp webhook moved %s → %s; disabling the stale one",
                                old_url,
                                self.webhook_url,
                            )
                            await client.post(
                                f"{self.base_url}/webhook/set/{self.instance}",
                                headers=self._headers(),
                                json={"webhook": {"enabled": False, "url": old_url, "events": []}},
                            )
                    except Exception as exc:  # best effort — never block the connect
                        logger.debug("could not clear the previous webhook: %s", exc)

                    await client.post(
                        f"{self.base_url}/webhook/set/{self.instance}",
                        headers=self._headers(),
                        json={
                            "webhook": {
                                "enabled": True,
                                "url": self.webhook_url,
                                "byEvents": False,
                                "base64": False,
                                "events": ["MESSAGES_UPSERT"],
                            }
                        },
                    )
        except Exception as exc:
            logger.warning("whatsapp connect failed: %s", exc)
            return False
        logger.info("whatsapp adapter ready (instance %s)", self.instance)
        return True

    async def disconnect(self) -> None:
        """Turn the webhook off, leaving the WhatsApp session paired.

        Deliberately not a logout: pairing costs a QR scan on the phone, so stopping the
        connector must not force one on the next start.
        """
        if not self.webhook_url:
            return
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{self.base_url}/webhook/set/{self.instance}",
                    headers=self._headers(),
                    json={"webhook": {"enabled": False, "url": self.webhook_url, "events": []}},
                )
        except Exception as exc:  # best effort — a dead Evolution isn't our problem here
            logger.debug("whatsapp webhook teardown failed: %s", exc)

    async def send(
        self, chat_id: str, text: str, *, thread_id: Optional[str] = None
    ) -> SendResult:
        # thread_id is ignored: WhatsApp has no thread concept the way Slack does, and
        # quoting a specific message needs the full message object, not just an id.
        return send_whatsapp(self.base_url, self.api_key, self.instance, chat_id, text)


def send_whatsapp(
    base_url: str, api_key: str, instance: str, chat_id: str, text: str
) -> SendResult:
    """One-shot outbound send. Sync, like the other senders — the engine runs it in a thread."""
    import httpx

    number = jid_to_number(chat_id)
    if not number:
        return SendResult(False, error="empty WhatsApp recipient")
    try:
        resp = httpx.post(
            f"{(base_url or '').rstrip('/')}/message/sendText/{instance}",
            headers={"apikey": api_key, "Content-Type": "application/json"},
            # Groups need the full JID; people are addressed by bare number.
            json={"number": chat_id if is_group(chat_id) else number, "text": text},
            timeout=30.0,
        )
        data = resp.json()
    except Exception as exc:
        return SendResult(False, error=str(exc))
    if resp.status_code >= 400:
        # The error body's shape varies by version; anything non-dict is reported as-is
        # rather than crashing on .get() while already handling a failure.
        detail = (
            (data.get("response") or data.get("message") or data)
            if isinstance(data, dict)
            else data
        )
        return SendResult(False, error=str(detail)[:200])
    key = data.get("key") if isinstance(data, dict) else None
    message_id = key.get("id") if isinstance(key, dict) else None
    return SendResult(True, message_id=str(message_id or ""))
