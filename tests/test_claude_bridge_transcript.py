"""Transcript reader — parses the ~/.claude/projects JSONL format.

Line shapes mirror the real Claude Code format (verified 2026-07-30): message lines
carry top-level type/timestamp/isSidechain/gitBranch and a message{role, content};
assistant content is a block list, user content a plain string.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from coworker.claude_bridge.transcript import (
    Entry,
    last_branch,
    parse_line,
    tail,
    wait_for_reply,
)


def _user_line(text: str, ts: str = "2026-07-30T10:00:00.000Z", **extra) -> str:
    data = {
        "type": "user",
        "timestamp": ts,
        "isSidechain": False,
        "gitBranch": "main",
        "message": {"role": "user", "content": text},
    }
    data.update(extra)
    return json.dumps(data)


def _assistant_line(text: str, ts: str = "2026-07-30T10:00:05.000Z", **extra) -> str:
    data = {
        "type": "assistant",
        "timestamp": ts,
        "isSidechain": False,
        "gitBranch": "main",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": text},
            ],
        },
    }
    data.update(extra)
    return json.dumps(data)


def test_parse_user_line():
    entry = parse_line(_user_line("run the tests"))
    assert entry == Entry(
        role="user",
        text="run the tests",
        timestamp=datetime(2026, 7, 30, 10, 0, 0, tzinfo=timezone.utc),
    )


def test_parse_assistant_line_joins_text_blocks_only():
    entry = parse_line(_assistant_line("done, 3 passed"))
    assert entry is not None
    assert entry.role == "assistant"
    assert entry.text == "done, 3 passed"  # thinking block excluded


def test_parse_skips_non_message_and_sidechain_and_garbage():
    assert parse_line('{"type": "mode", "mode": "normal"}') is None
    assert parse_line(_assistant_line("sub", isSidechain=True)) is None
    assert parse_line('{"truncated": ') is None  # write in progress — never raises
    assert parse_line("") is None


def test_parse_skips_entries_without_visible_text():
    # tool_result-only user line: content is a block list with no text blocks
    line = json.dumps(
        {
            "type": "user",
            "timestamp": "2026-07-30T10:00:00.000Z",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "x"}],
            },
        }
    )
    assert parse_line(line) is None


def test_tail_returns_last_n_parsed_entries(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    lines = ['{"type": "mode"}']
    for i in range(5):
        lines.append(_user_line(f"msg {i}", ts=f"2026-07-30T10:00:0{i}.000Z"))
    path.write_text("\n".join(lines), encoding="utf-8")
    entries = tail(path, n=2)
    assert [e.text for e in entries] == ["msg 3", "msg 4"]


def test_tail_missing_file_is_empty():
    assert tail(Path("/nonexistent/nope.jsonl")) == []


def test_last_branch_takes_latest(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    path.write_text(
        _user_line("a", gitBranch="main")
        + "\n"
        + _assistant_line("b", gitBranch="fix/webhook")
        + "\n",
        encoding="utf-8",
    )
    assert last_branch(path) == "fix/webhook"
    assert last_branch(Path("/nonexistent/nope.jsonl")) is None


class _Clock:
    """Fake monotonic clock + sleep: sleeping advances time, no real waiting."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_wait_for_reply_returns_settled_reply(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    path.write_text(_user_line("q", ts="2026-07-30T10:00:00.000Z"), encoding="utf-8")
    after = datetime(2026, 7, 30, 10, 0, 1, tzinfo=timezone.utc)
    fake = _Clock()
    orig_sleep = fake.sleep

    def sleep_and_reply(seconds: float) -> None:
        orig_sleep(seconds)
        if fake.now >= 2.0 and "answer" not in path.read_text(encoding="utf-8"):
            with path.open("a", encoding="utf-8") as f:
                f.write("\n" + _assistant_line("answer", ts="2026-07-30T10:00:10.000Z"))

    reply = wait_for_reply(
        path,
        after,
        timeout=60.0,
        poll=1.0,
        settle=5.0,
        sleep=sleep_and_reply,
        clock=fake.clock,
    )
    assert reply == "answer"
    # settle respected: returned only after 5 quiet seconds past the reply
    assert fake.now >= 7.0


def test_wait_for_reply_ignores_older_entries_and_times_out(tmp_path: Path):
    path = tmp_path / "s.jsonl"
    path.write_text(
        _assistant_line("old", ts="2026-07-30T09:00:00.000Z"), encoding="utf-8"
    )
    after = datetime(2026, 7, 30, 10, 0, 0, tzinfo=timezone.utc)
    fake = _Clock()
    reply = wait_for_reply(
        path, after, timeout=10.0, poll=1.0, sleep=fake.sleep, clock=fake.clock
    )
    assert reply is None
    assert fake.now >= 10.0


def test_wait_for_reply_timeout_returns_partial(tmp_path: Path):
    # A reply arrived but never settled before timeout → return it anyway.
    path = tmp_path / "s.jsonl"
    path.write_text(
        _assistant_line("partial", ts="2026-07-30T10:00:10.000Z"), encoding="utf-8"
    )
    after = datetime(2026, 7, 30, 10, 0, 0, tzinfo=timezone.utc)
    fake = _Clock()
    reply = wait_for_reply(
        path,
        after,
        timeout=3.0,
        poll=1.0,
        settle=60.0,
        sleep=fake.sleep,
        clock=fake.clock,
    )
    assert reply == "partial"
