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

    def find_target(self, tty: str) -> str | None:
        return self.target

    def send_text(self, target: str, text: str) -> bool:
        self.sent.append((target, text))
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


def test_send_without_transcript_reports_sent_no_reply():
    t = _tools(
        FakeDiscovery([_session(transcript=None)]), FakeDriver(), now=lambda: NOW
    )
    result = t["send_to_claude_session"](tty="ttys000", text="x")
    assert result == {"status": "sent_no_reply", "last_entries": []}


def test_default_factory_builds_three_tools():
    names = {t.__name__ for t in claude_bridge_tools()}
    assert names == {
        "find_claude_sessions",
        "read_claude_transcript",
        "send_to_claude_session",
    }
