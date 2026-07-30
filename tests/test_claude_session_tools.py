"""Tool adapter — every error contract from the spec's table, with fakes for all deps."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from coworker.claude_bridge.models import LiveSession
from coworker.tools.claude_sessions import claude_bridge_tools, claude_session_tools

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


def _session(tty: str = "ttys000", transcript: Path | None = None) -> LiveSession:
    return LiveSession(
        pid=910,
        tty=tty,
        cwd="/Users/x/dev/webhook",
        branch="fix/webhook",
        transcript=transcript,
        transcript_confidence="matched" if transcript else "none",
        last_activity=NOW,
        tail="assistant: done",
    )


class FakeDiscovery:
    def __init__(self, sessions):
        self.sessions = sessions

    def list(self):
        return self.sessions


class FakeDriver:
    def __init__(self, target: str | None = "w0t0p0:ABC", send_ok: bool = True):
        self.target = target
        self.send_ok = send_ok
        self.sent: list[tuple[str, str]] = []
        self.keys: list[tuple[str, str]] = []

    def find_target(self, tty: str) -> str | None:
        return self.target

    def send_text(self, target: str, text: str) -> bool:
        self.sent.append((target, text))
        return self.send_ok

    def send_keys(self, target: str, keys: str) -> bool:
        self.keys.append((target, keys))
        return self.send_ok


def _tools(discovery, driver, **kwargs) -> dict:
    tools = claude_session_tools(discovery, driver, **kwargs)
    return {t.__name__: t for t in tools}


def _write_transcript(tmp_path: Path, texts: list[str]) -> Path:
    path = tmp_path / "s.jsonl"
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "timestamp": f"2026-07-30T12:00:1{i}.000Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            }
        )
        for i, text in enumerate(texts)
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_every_tool_has_a_schema():
    for tool in claude_session_tools(FakeDiscovery([]), FakeDriver()):
        schema = tool.__coworker_schema__
        assert schema["function"]["name"] == tool.__name__


def test_find_returns_serialised_sessions(tmp_path: Path):
    t = _tools(
        FakeDiscovery([_session(transcript=_write_transcript(tmp_path, ["hi"]))]),
        FakeDriver(),
    )
    result = t["find_claude_sessions"]()
    assert result["sessions"][0]["project"] == "webhook"


def test_find_empty_carries_a_hint():
    result = _tools(FakeDiscovery([]), FakeDriver())["find_claude_sessions"]()
    assert result["sessions"] == []
    assert "hint" in result


def test_read_returns_parsed_tail(tmp_path: Path):
    path = _write_transcript(tmp_path, ["first", "second"])
    t = _tools(FakeDiscovery([_session(transcript=path)]), FakeDriver())
    result = t["read_claude_transcript"](tty="ttys000", n=1)
    assert result["entries"] == [
        {
            "role": "assistant",
            "text": "second",
            "timestamp": "2026-07-30T12:00:11+00:00",
        }
    ]


def test_read_unknown_tty_is_session_gone():
    t = _tools(FakeDiscovery([_session(tty="ttys000")]), FakeDriver())
    assert t["read_claude_transcript"](tty="ttys999") == {"error": "session_gone"}


def test_read_without_transcript():
    t = _tools(FakeDiscovery([_session(transcript=None)]), FakeDriver())
    assert t["read_claude_transcript"](tty="ttys000") == {"error": "no_transcript"}


def test_read_rejects_bad_arguments_and_caps_n(tmp_path: Path):
    path = _write_transcript(tmp_path, ["a"])
    t = _tools(FakeDiscovery([_session(transcript=path)]), FakeDriver())
    assert t["read_claude_transcript"](tty="") == {"error": "invalid_arguments"}
    assert t["read_claude_transcript"](tty="   ") == {"error": "invalid_arguments"}
    assert t["read_claude_transcript"](tty=123) == {"error": "invalid_arguments"}
    # booleans pass isinstance(x, int) — n=True must fall back to the default, not 1
    many = tmp_path / "many.jsonl"
    many.write_text(
        "\n".join(
            json.dumps(
                {
                    "type": "assistant",
                    "timestamp": f"2026-07-30T12:{i // 60:02d}:{i % 60:02d}.000Z",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"m{i}"}],
                    },
                }
            )
            for i in range(150)
        ),
        encoding="utf-8",
    )
    t = _tools(FakeDiscovery([_session(transcript=many)]), FakeDriver())
    assert len(t["read_claude_transcript"](tty="ttys000", n=True)["entries"]) == 20
    # a huge n must not dump a whole transcript: capped at 100, file has 150
    result = t["read_claude_transcript"](tty="ttys000", n=10_000)
    assert len(result["entries"]) == 100


def test_send_replies(tmp_path: Path):
    path = _write_transcript(tmp_path, ["earlier"])
    driver = FakeDriver()
    t = _tools(
        FakeDiscovery([_session(transcript=path)]),
        driver,
        waiter=lambda p, after, timeout: "the answer",
        now=lambda: NOW,
    )
    result = t["send_to_claude_session"](tty="ttys000", text="status?")
    assert result == {"status": "replied", "reply": "the answer"}
    assert driver.sent == [("w0t0p0:ABC", "status?")]


def test_send_no_reply_returns_partial_state(tmp_path: Path):
    path = _write_transcript(tmp_path, ["earlier"])
    t = _tools(
        FakeDiscovery([_session(transcript=path)]),
        FakeDriver(),
        waiter=lambda p, after, timeout: None,
        now=lambda: NOW,
    )
    result = t["send_to_claude_session"](tty="ttys000", text="status?", wait_seconds=5)
    assert result["status"] == "sent_no_reply"
    assert result["last_entries"] == ["earlier"]


def test_send_tab_gone_between_find_and_send():
    t = _tools(FakeDiscovery([_session()]), FakeDriver(target=None))
    assert t["send_to_claude_session"](tty="ttys000", text="x") == {
        "error": "session_gone"
    }


def test_send_write_failure():
    t = _tools(FakeDiscovery([_session()]), FakeDriver(send_ok=False))
    assert t["send_to_claude_session"](tty="ttys000", text="x") == {
        "error": "send_failed"
    }


def test_send_rejects_blank_text_and_bad_types():
    driver = FakeDriver()
    t = _tools(FakeDiscovery([_session()]), driver)
    assert t["send_to_claude_session"](tty="ttys000", text="   ") == {
        "error": "invalid_arguments"
    }
    assert t["send_to_claude_session"](tty="ttys000", text=None) == {
        "error": "invalid_arguments"
    }
    assert t["send_to_claude_session"](tty="", text="hi") == {
        "error": "invalid_arguments"
    }
    assert driver.sent == []  # nothing reached the terminal


def test_send_caps_wait_seconds(tmp_path: Path):
    path = _write_transcript(tmp_path, ["earlier"])
    waits: list[float] = []

    def waiter(p, after, timeout):
        waits.append(timeout)
        return "ok"

    t = _tools(
        FakeDiscovery([_session(transcript=path)]),
        FakeDriver(),
        waiter=waiter,
        now=lambda: NOW,
    )
    t["send_to_claude_session"](tty="ttys000", text="x", wait_seconds=999_999)
    assert waits == [600.0]  # capped — a turn can't be pinned indefinitely
    # wait_seconds=True passes isinstance(x, int): must mean the default, not 1 second
    t["send_to_claude_session"](tty="ttys000", text="x", wait_seconds=True)
    assert waits == [600.0, 120.0]


def test_send_without_transcript_reports_sent_no_reply():
    t = _tools(
        FakeDiscovery([_session(transcript=None)]), FakeDriver(), now=lambda: NOW
    )
    result = t["send_to_claude_session"](tty="ttys000", text="x")
    assert result == {"status": "sent_no_reply", "last_entries": []}


def test_default_factory_builds_all_bridge_tools():
    names = {t.__name__ for t in claude_bridge_tools()}
    assert names == {
        "find_claude_sessions",
        "read_claude_transcript",
        "send_to_claude_session",
        "watch_claude_session",
        "unwatch_claude_session",
        "respond_to_claude_prompt",
    }


def _session_with_id(tty: str = "ttys000", session_id: str | None = "sess-1"):
    s = _session(tty=tty)
    s.session_id = session_id
    s.status = "idle" if session_id else None
    return s


def _watched_tools(tmp_path, sessions):
    from coworker.claude_bridge.registry import Watches

    watches = Watches(tmp_path)
    tools = claude_session_tools(FakeDiscovery(sessions), FakeDriver(), watches=watches)
    return {t.__name__: t for t in tools}, watches


def test_watch_tools_absent_without_watches():
    names = {t.__name__ for t in claude_session_tools(FakeDiscovery([]), FakeDriver())}
    assert "watch_claude_session" not in names


def test_watch_records_notify_target(tmp_path):
    t, watches = _watched_tools(tmp_path, [_session_with_id()])
    result = t["watch_claude_session"](
        tty="ttys000", notify_target="whatsapp_evolution:5511@s.whatsapp.net"
    )
    assert result == {"status": "watching", "session_id": "sess-1"}
    watch = watches.get("sess-1")
    assert watch["platform"] == "whatsapp_evolution"
    assert watch["chat_id"] == "5511@s.whatsapp.net"


def test_watch_errors(tmp_path):
    t, _ = _watched_tools(tmp_path, [_session_with_id()])
    assert t["watch_claude_session"](
        tty="ttys9", notify_target="whatsapp_evolution:x"
    ) == {"error": "session_gone"}
    assert t["watch_claude_session"](tty="ttys000", notify_target="no-colon") == {
        "error": "invalid_arguments"
    }
    assert t["watch_claude_session"](tty="ttys000", notify_target="") == {
        "error": "invalid_arguments"
    }
    t["watch_claude_session"](tty="ttys000", notify_target="whatsapp_evolution:x")
    assert t["watch_claude_session"](
        tty="ttys000", notify_target="whatsapp_evolution:x"
    ) == {"error": "already_watched"}


def test_watch_rejects_unknown_platform(tmp_path):
    t, _ = _watched_tools(tmp_path, [_session_with_id()])
    result = t["watch_claude_session"](tty="ttys000", notify_target="carrier-pigeon:x")
    assert result["error"] == "unknown_platform"
    assert "whatsapp_evolution" in result["hint"]


def test_watch_without_registry_session_id(tmp_path):
    t, _ = _watched_tools(tmp_path, [_session_with_id(session_id=None)])
    result = t["watch_claude_session"](
        tty="ttys000", notify_target="whatsapp_evolution:x"
    )
    assert result["error"] == "no_registry"
    assert "coworker.claude_bridge.install" in result["hint"]


def test_unwatch_is_idempotent(tmp_path):
    t, _ = _watched_tools(tmp_path, [_session_with_id()])
    t["watch_claude_session"](tty="ttys000", notify_target="whatsapp_evolution:x")
    assert t["unwatch_claude_session"](tty="ttys000") == {"status": "unwatched"}
    assert t["unwatch_claude_session"](tty="ttys000") == {"status": "not_watched"}


def test_find_reports_watched_and_status(tmp_path):
    t, watches = _watched_tools(tmp_path, [_session_with_id()])
    watches.add("sess-1", "whatsapp_evolution", "x")
    (entry,) = t["find_claude_sessions"]()["sessions"]
    assert entry["watched"] is True
    assert entry["status"] == "idle"
    assert entry["session_id"] == "sess-1"


def _write_bridge_state(
    bridge: Path,
    session_id: str,
    *,
    status: str = "waiting_approval",
    message: str | None = "permission to run: git push",
    transcript_path: str | None = None,
) -> None:
    sessions = bridge / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "transcript_path": transcript_path,
                "cwd": "/Users/x/dev/webhook",
                "pid": 910,
                "status": status,
                "message": message,
                "updated_at": "2026-07-30T15:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def _waiting_session(tmp_path, message="permission to run: git push",
                     session_id="sess-1", status="waiting_approval"):
    s = _session_with_id(session_id=session_id)
    s.status = status
    t = _write_transcript(tmp_path, ["before"])
    s.transcript = t
    _write_bridge_state(
        tmp_path, session_id, status=status, message=message, transcript_path=str(t)
    )
    return s


def _respond_tools(tmp_path, sessions, driver=None, clock=None, sleep=None):
    tools = claude_session_tools(
        FakeDiscovery(sessions),
        driver or FakeDriver(),
        bridge_dir=tmp_path,
        clock=clock or iter(range(1000)).__next__,
        sleep=sleep or (lambda s: None),
    )
    return {t.__name__: t for t in tools}


def test_respond_absent_without_bridge_dir():
    names = {t.__name__ for t in claude_session_tools(FakeDiscovery([]), FakeDriver())}
    assert "respond_to_claude_prompt" not in names


def test_respond_first_call_echoes_prompt_and_token(tmp_path):
    s = _waiting_session(tmp_path)
    t = _respond_tools(tmp_path, [s])
    result = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")
    assert result["status"] == "confirmation_required"
    assert result["prompt"] == "permission to run: git push"
    assert len(result["confirm_token"]) == 12


def test_respond_approve_with_token_presses_1_and_verifies(tmp_path):
    import os as _os

    s = _waiting_session(tmp_path)
    driver = FakeDriver()

    def sleep(_):
        # the approved command ran: the transcript moves
        _os.utime(s.transcript, (9_999_999_999, 9_999_999_999))

    t = _respond_tools(tmp_path, [s], driver=driver, sleep=sleep)
    token = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")[
        "confirm_token"
    ]
    result = t["respond_to_claude_prompt"](
        tty="ttys000", decision="approve", confirm_token=token
    )
    assert result["status"] == "approved"
    assert result["verified"] is True
    assert driver.keys == [("w0t0p0:ABC", "1")]


def test_respond_deny_presses_3_and_verifies(tmp_path):
    import os as _os

    s = _waiting_session(tmp_path)
    driver = FakeDriver()

    def sleep(_):
        # the denial reached Claude: the turn continues, the transcript moves
        _os.utime(s.transcript, (9_999_999_999, 9_999_999_999))

    t = _respond_tools(tmp_path, [s], driver=driver, sleep=sleep)
    token = t["respond_to_claude_prompt"](tty="ttys000", decision="deny")[
        "confirm_token"
    ]
    result = t["respond_to_claude_prompt"](
        tty="ttys000", decision="deny", confirm_token=token
    )
    assert result["status"] == "denied"
    assert result["verified"] is True
    assert driver.keys == [("w0t0p0:ABC", "3")]


def test_respond_stale_token_is_prompt_changed(tmp_path):
    s = _waiting_session(tmp_path)
    t = _respond_tools(tmp_path, [s])
    result = t["respond_to_claude_prompt"](
        tty="ttys000", decision="approve", confirm_token="deadbeef0000"
    )
    assert result["error"] == "prompt_changed"
    assert len(result["confirm_token"]) == 12


def test_respond_not_waiting_reports_current_status(tmp_path):
    s = _waiting_session(tmp_path, status="idle")
    t = _respond_tools(tmp_path, [s])
    result = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")
    assert result == {"error": "not_waiting", "current_status": "idle"}


def test_respond_no_registry(tmp_path):
    s = _session_with_id(session_id=None)
    t = _respond_tools(tmp_path, [s])
    result = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")
    assert result["error"] == "no_registry"


def test_respond_unverified_when_transcript_frozen(tmp_path):
    s = _waiting_session(tmp_path)
    t = _respond_tools(tmp_path, [s])
    token = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")[
        "confirm_token"
    ]
    result = t["respond_to_claude_prompt"](
        tty="ttys000", decision="approve", confirm_token=token
    )
    assert result["status"] == "sent_unverified"


def test_respond_empty_message_echoes_fallback(tmp_path):
    s = _waiting_session(tmp_path, message=None)
    t = _respond_tools(tmp_path, [s])
    result = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")
    assert result["status"] == "confirmation_required"
    assert result["prompt"] == "a permission request"


def test_respond_invalid_arguments(tmp_path):
    s = _waiting_session(tmp_path)
    t = _respond_tools(tmp_path, [s])
    assert t["respond_to_claude_prompt"](tty="", decision="approve") == {
        "error": "invalid_arguments"
    }
    assert t["respond_to_claude_prompt"](tty="ttys000", decision="shrug") == {
        "error": "invalid_arguments"
    }


def test_respond_session_gone_and_send_failed(tmp_path):
    s = _waiting_session(tmp_path)
    t = _respond_tools(tmp_path, [s], driver=FakeDriver(target=None))
    token_result = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")
    result = t["respond_to_claude_prompt"](
        tty="ttys000",
        decision="approve",
        confirm_token=token_result["confirm_token"],
    )
    assert result == {"error": "session_gone"}

    t2 = _respond_tools(tmp_path, [s], driver=FakeDriver(send_ok=False))
    token = t2["respond_to_claude_prompt"](tty="ttys000", decision="approve")[
        "confirm_token"
    ]
    assert t2["respond_to_claude_prompt"](
        tty="ttys000", decision="approve", confirm_token=token
    ) == {"error": "send_failed"}


def _assistant_tool_names(tmp_path, monkeypatch, platform: str) -> set[str]:
    """The Assistant persona's built toolset under a given platform — through
    build_engine, so the test proves the wiring, not a source substring."""
    import sys

    from coworker.agent import build_engine
    from coworker.personas.registry import PersonaRegistry
    from coworker.secrets import SecretStore

    monkeypatch.setattr(sys, "platform", platform)
    engine = build_engine(
        agent=PersonaRegistry().agent("assistant"),
        workspace=None,
        secrets=SecretStore(tmp_path / f"secrets-{platform}.json"),
    )
    return set(engine.registry.names())


def test_bridge_registered_for_messaging_persona_on_macos_only(tmp_path, monkeypatch):
    bridge = {
        "find_claude_sessions",
        "read_claude_transcript",
        "send_to_claude_session",
    }
    assert bridge <= _assistant_tool_names(tmp_path, monkeypatch, "darwin")
    assert not bridge & _assistant_tool_names(tmp_path, monkeypatch, "linux")
