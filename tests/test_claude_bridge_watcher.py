"""Watcher — fake registry dir, fake notifier, injected liveness. No asyncio needed:
poll_once() is the whole behaviour; start()/stop() are a thin loop around it."""

from __future__ import annotations

import json
from pathlib import Path

from coworker.claude_bridge.registry import Watches
from coworker.claude_bridge.watcher import BridgeWatcher, ConnectorNotifier


def _write_state(bridge: Path, session_id: str, **overrides) -> None:
    state = {
        "session_id": session_id,
        "transcript_path": None,
        "cwd": "/Users/x/dev/webhook",
        "pid": 910,
        "status": "idle",
        "message": None,
        "updated_at": "2026-07-30T15:00:00+00:00",
    }
    state.update(overrides)
    sessions = bridge / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.json").write_text(json.dumps(state), encoding="utf-8")


def _transcript(tmp_path: Path, text: str) -> str:
    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-07-30T15:00:00.000Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            }
        ),
        encoding="utf-8",
    )
    return str(path)


class FakeNotifier:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.sent: list[tuple[str, str, str]] = []

    def send(self, platform: str, chat_id: str, text: str) -> bool:
        self.sent.append((platform, chat_id, text))
        return self.ok


def _watch(bridge: Path, session_id: str = "aaa") -> None:
    Watches(bridge).add(session_id, "whatsapp_evolution", "5511@s.whatsapp.net")


def test_idle_watched_session_notifies_once_and_consumes(tmp_path: Path):
    # Session is running when the watch is placed, then goes idle.
    _write_state(tmp_path, "aaa", status="waiting_approval")
    _watch(tmp_path)
    notifier = FakeNotifier()
    watcher = BridgeWatcher(tmp_path, notifier, alive=lambda pid: True)
    watcher.poll_once()  # baseline: waiting_approval → notifies the waiting state
    _write_state(
        tmp_path,
        "aaa",
        status="idle",
        transcript_path=_transcript(tmp_path, "refactor done, 12 files"),
    )
    watcher.poll_once()
    finished = [s for s in notifier.sent if "finished" in s[2]]
    assert len(finished) == 1
    platform, chat_id, text = finished[0]
    assert (platform, chat_id) == ("whatsapp_evolution", "5511@s.whatsapp.net")
    assert "webhook" in text  # project name
    assert "refactor done, 12 files" in text  # transcript snippet
    watcher.poll_once()
    assert len([s for s in notifier.sent if "finished" in s[2]]) == 1  # consumed
    assert Watches(tmp_path).get("aaa") is None


def test_session_already_idle_when_watched_notifies_on_first_poll(tmp_path: Path):
    # Watch placed after the turn ended (registry already says idle): first poll fires.
    _write_state(tmp_path, "aaa", status="idle")
    _watch(tmp_path)
    notifier = FakeNotifier()
    BridgeWatcher(tmp_path, notifier, alive=lambda pid: True).poll_once()
    assert len(notifier.sent) == 1


def test_waiting_approval_notifies_without_consuming(tmp_path: Path):
    _write_state(
        tmp_path, "aaa", status="waiting_approval", message="permission to run: git push"
    )
    _watch(tmp_path)
    notifier = FakeNotifier()
    watcher = BridgeWatcher(tmp_path, notifier, alive=lambda pid: True)
    watcher.poll_once()
    assert len(notifier.sent) == 1
    assert "git push" in notifier.sent[0][2]
    assert Watches(tmp_path).get("aaa") is not None  # still watched
    watcher.poll_once()
    assert len(notifier.sent) == 1  # same state → no repeat


def test_two_different_approvals_both_notify(tmp_path: Path):
    # Dedup keys on (status, message): a second, different permission request must
    # not be swallowed just because the status is the same.
    _write_state(tmp_path, "aaa", status="waiting_approval", message="run: git push")
    _watch(tmp_path)
    notifier = FakeNotifier()
    watcher = BridgeWatcher(tmp_path, notifier, alive=lambda pid: True)
    watcher.poll_once()
    _write_state(
        tmp_path, "aaa", status="waiting_approval", message="run: docker compose up"
    )
    watcher.poll_once()
    assert len(notifier.sent) == 2
    assert "docker compose up" in notifier.sent[1][2]


def test_ghost_watched_session_notifies_closed_and_consumes(tmp_path: Path):
    _write_state(tmp_path, "aaa", status="idle", pid=999)
    _watch(tmp_path)
    notifier = FakeNotifier()
    BridgeWatcher(tmp_path, notifier, alive=lambda pid: False).poll_once()
    assert len(notifier.sent) == 1
    assert "closed" in notifier.sent[0][2]
    assert Watches(tmp_path).get("aaa") is None
    assert not list((tmp_path / "sessions").glob("*.json"))  # pruned


def test_unwatched_sessions_never_notify(tmp_path: Path):
    _write_state(tmp_path, "aaa", status="idle")
    _write_state(tmp_path, "bbb", status="waiting_approval")
    notifier = FakeNotifier()
    BridgeWatcher(tmp_path, notifier, alive=lambda pid: True).poll_once()
    assert notifier.sent == []


def test_notifier_failure_does_not_crash_and_watch_survives(tmp_path: Path):
    _write_state(tmp_path, "aaa", status="idle")
    _watch(tmp_path)
    notifier = FakeNotifier(ok=False)
    watcher = BridgeWatcher(tmp_path, notifier, alive=lambda pid: True)
    watcher.poll_once()  # send fails → watch NOT consumed, retried next poll
    assert Watches(tmp_path).get("aaa") is not None
    notifier.ok = True
    watcher.poll_once()
    assert Watches(tmp_path).get("aaa") is None


def test_empty_registry_is_a_noop(tmp_path: Path):
    notifier = FakeNotifier()
    BridgeWatcher(tmp_path, notifier, alive=lambda pid: True).poll_once()
    assert notifier.sent == []


class _FakeSecrets:
    def __init__(self, creds: dict):
        self._creds = creds

    def get(self, key: str):
        return self._creds.get(key)


def test_connector_notifier_sends_via_sender_registry():
    calls: list[tuple] = []

    def fake_sender(token, chat_id, text, thread_id=None):
        calls.append((token, chat_id, text, thread_id))

        class R:
            ok = True

        return R()

    secrets = _FakeSecrets(
        {
            "whatsapp_evolution:default": {
                "base_url": "http://e",
                "api_key": "k",
                "instance": "openworker",
            }
        }
    )
    notifier = ConnectorNotifier(secrets, senders={"whatsapp_evolution": fake_sender})
    assert notifier.send("whatsapp_evolution", "5511@s.whatsapp.net", "hi") is True
    assert calls == [("http://e|k|openworker", "5511@s.whatsapp.net", "hi", None)]


def test_connector_notifier_false_on_missing_token_or_platform():
    notifier = ConnectorNotifier(_FakeSecrets({}), senders={})
    assert notifier.send("whatsapp_evolution", "5511@x", "hi") is False


def test_connector_notifier_false_on_sender_crash():
    def broken(token, chat_id, text, thread_id=None):
        raise RuntimeError("boom")

    secrets = _FakeSecrets({"whatsapp_evolution:default": {"base_url": "http://e"}})
    notifier = ConnectorNotifier(secrets, senders={"whatsapp_evolution": broken})
    assert notifier.send("whatsapp_evolution", "5511@x", "hi") is False
