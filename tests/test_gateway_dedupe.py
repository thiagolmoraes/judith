"""Inbound de-duplication.

Real incident (2026-07-31): while a scheduled run was stuck, WhatsApp messages queued
behind it and were then delivered in a burst — the SAME message seven times, plus an
older one re-delivered next to a newer one. The agent, seeing a repeated "delete it, I
already did", applied it to the wrong reminder and then claimed success twice without
calling any tool at all.

A message id identifies a message exactly once. Delivering the same id twice is always
the transport repeating itself, never the person saying it again.
"""

import asyncio

from coworker.connectors.base import MessageEvent, SessionSource
from coworker.connectors.config import ConnectorSettings
from coworker.connectors.gateway import Gateway


def _event(text: str, message_id: str, user="5511999999999") -> MessageEvent:
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform="whatsapp_evolution",
            chat_id=f"{user}@s.whatsapp.net",
            user_id=user,
            user_name="Test Contact",
            chat_type="dm",
        ),
        message_id=message_id,
        mentions_me=True,
    )


def _gateway(seen: list):
    async def handler(event):
        seen.append(event)

    gw = Gateway(handler=handler)
    gw.settings = {
        "whatsapp_evolution": ConnectorSettings(
            platform="whatsapp_evolution", enabled=True, allow_all=True
        )
    }
    return gw


def test_the_same_message_id_is_handled_once():
    seen: list = []
    gw = _gateway(seen)

    asyncio.run(gw._on_inbound(_event("Pode deletar, já fiz", "WAMID1")))
    for _ in range(6):  # the burst that actually happened
        asyncio.run(gw._on_inbound(_event("Pode deletar, já fiz", "WAMID1")))

    assert len(seen) == 1


def test_identical_text_with_different_ids_is_two_messages():
    # Someone repeating themselves deliberately must still get through — only the
    # transport's own repeats are dropped.
    seen: list = []
    gw = _gateway(seen)

    asyncio.run(gw._on_inbound(_event("oi", "WAMID1")))
    asyncio.run(gw._on_inbound(_event("oi", "WAMID2")))

    assert len(seen) == 2


def test_messages_without_an_id_are_never_dropped():
    # Some adapters/versions omit it; a missing id must not collapse distinct messages.
    seen: list = []
    gw = _gateway(seen)

    asyncio.run(gw._on_inbound(_event("um", "")))
    asyncio.run(gw._on_inbound(_event("dois", "")))

    assert len(seen) == 2


def test_the_same_id_on_two_platforms_is_two_messages():
    seen: list = []
    gw = _gateway(seen)
    gw.settings["telegram"] = ConnectorSettings(
        platform="telegram", enabled=True, allow_all=True
    )

    first = _event("oi", "42")
    second = _event("oi", "42")
    second.source.platform = "telegram"

    asyncio.run(gw._on_inbound(first))
    asyncio.run(gw._on_inbound(second))

    assert len(seen) == 2


def test_the_dedupe_memory_is_bounded():
    seen: list = []
    gw = _gateway(seen)

    for i in range(600):
        asyncio.run(gw._on_inbound(_event("x", f"WAMID{i}")))

    assert len(seen) == 600
    assert len(gw._seen_message_ids) <= 512
