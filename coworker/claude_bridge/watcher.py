"""The bridge watcher — turns registry transitions into WhatsApp notifications.

Runs as an asyncio task in the always-on server (started with the gateway, macOS
only), polling the registry every couple of seconds. Only *watched* sessions ever
notify (spec: opt-in, one-shot — notify-every-turn would spam a chatty terminal).
The notification is a template plus a transcript snippet: no model turn, no cost.

Send failures leave the watch in place — the next poll retries. A session that died
without finishing (pid gone, SessionEnd never fired) notifies "closed before
finishing" and consumes the watch.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Callable, Optional, Protocol

from .registry import SessionState, Watches, prune, read_sessions
from .transcript import last_branch, tail

logger = logging.getLogger("coworker.claude_bridge")

_SNIPPET_CHARS = 300


class Notifier(Protocol):
    def send(self, platform: str, chat_id: str, text: str) -> bool: ...


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _project(state: SessionState) -> str:
    name = Path(state.cwd).name if state.cwd else state.session_id[:8]
    branch = last_branch(state.transcript_path) if state.transcript_path else None
    return f"{name} ({branch})" if branch else name


def _snippet(state: SessionState) -> str:
    if state.transcript_path is None:
        return ""
    entries = [e for e in tail(state.transcript_path, 5) if e.role == "assistant"]
    if not entries:
        return ""
    return entries[-1].text[:_SNIPPET_CHARS]


class BridgeWatcher:
    def __init__(
        self,
        bridge_dir: Path,
        notifier: Notifier,
        *,
        alive: Optional[Callable[[int], bool]] = None,
        poll_seconds: float = 2.0,
    ) -> None:
        self._dir = bridge_dir
        self._notifier = notifier
        self._alive = alive or _pid_alive
        self._poll_seconds = poll_seconds
        self._watches = Watches(bridge_dir)
        # session_id → last (status, message) we notified for. The message is part of
        # the key: two different approval requests in a row must both come through.
        self._notified: dict[str, tuple[str, Optional[str]]] = {}
        self._task: Optional[asyncio.Task] = None

    # -- one poll: the entire behaviour, synchronous and testable ------------------

    def poll_once(self) -> None:
        try:
            self._poll()
        except Exception:  # never let a bad poll kill the loop
            logger.exception("bridge watcher poll failed")

    def _poll(self) -> None:
        for state in prune(self._dir, self._alive):
            watch = self._watches.get(state.session_id)
            if watch is None:
                continue
            text = f"⚠️ Claude session '{_project(state)}' closed before finishing."
            if self._notifier.send(watch["platform"], watch["chat_id"], text):
                self._watches.pop(state.session_id)
            self._notified.pop(state.session_id, None)

        for state in read_sessions(self._dir):
            watch = self._watches.get(state.session_id)
            if watch is None:
                self._notified.pop(state.session_id, None)
                continue
            if self._notified.get(state.session_id) == (state.status, state.message):
                continue
            if state.status == "idle":
                snippet = _snippet(state)
                text = f"✅ Claude session '{_project(state)}' finished."
                if snippet:
                    text = f"{text}\n\n{snippet}"
                if self._notifier.send(watch["platform"], watch["chat_id"], text):
                    self._watches.pop(state.session_id)
                    self._notified[state.session_id] = (state.status, state.message)
            elif state.status == "waiting_approval":
                detail = state.message or "a permission request"
                text = (
                    f"⏸ Claude session '{_project(state)}' is waiting for approval: "
                    f"{detail}"
                )
                if self._notifier.send(watch["platform"], watch["chat_id"], text):
                    # Watch NOT consumed — the session hasn't finished.
                    self._notified[state.session_id] = (state.status, state.message)

    # -- lifecycle (mirrors automation.Scheduler) -----------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_event_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            # poll_once does file I/O and a synchronous HTTP send (up to the sender's
            # 30 s timeout) — keep it off the server's event loop.
            await asyncio.to_thread(self.poll_once)
            await asyncio.sleep(self._poll_seconds)


class ConnectorNotifier:
    """Production Notifier: the same stateless senders `send_message` uses."""

    def __init__(self, secrets, senders: Optional[dict] = None) -> None:
        from ..connectors.senders import DEFAULT_SENDERS

        self._secrets = secrets
        self._senders = DEFAULT_SENDERS if senders is None else senders

    def send(self, platform: str, chat_id: str, text: str) -> bool:
        from ..connectors.tools import _resolve_token

        sender = self._senders.get(platform)
        if sender is None:
            return False
        token = _resolve_token(self._secrets, platform, chat_id)
        if not token:
            return False
        try:
            return bool(sender(token, chat_id, text, None).ok)
        except Exception:
            logger.exception("bridge notification send failed (%s)", platform)
            return False
