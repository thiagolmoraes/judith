# WhatsApp ⇄ Claude Code Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the WhatsApp-facing Assistant session find live Claude Code CLI sessions on this Mac, read their transcripts, and send them input, so the owner can query/steer them from WhatsApp.

**Architecture:** A new domain package `coworker/claude_bridge/` (models, discovery, transcript, terminal driver) plus one thin tool adapter `coworker/tools/claude_sessions.py` exposing three tools. Tools are registered in `coworker/agent.py` for messaging personas on macOS only. Spec: `docs/superpowers/specs/2026-07-30-whatsapp-claude-bridge-design.md`.

**Tech Stack:** Python ≥3.10, stdlib only (`subprocess`, `json`, `pathlib`, `datetime`), `osascript`/AppleScript for iTerm2, pytest.

## Global Constraints

- Python ≥3.10 (`str | None` unions OK). Run tests from repo root with the venv: `source .venv/bin/activate`.
- **No test may execute real `ps`, `lsof`, `osascript`, or touch iTerm2 / `~/.claude`.** Everything external is injected (fake runner, `tmp_path`, fake clock/sleep). Suite must pass on Linux CI.
- Domain modules (`coworker/claude_bridge/*`) must not import from `coworker.tools`, `coworker.engine`, or `coworker.agent` (dependencies point inward).
- Drivers never raise for "not found": `find_target` returns `None`, `send_text` returns `False`. Subprocess calls always pass `timeout=10`.
- Commits: message style matches repo history (imperative sentence, no `feat:` prefix), **no `Co-Authored-By` trailer** (owner rule).
- macOS-only feature: registration gated by `sys.platform == "darwin"`; the modules themselves stay importable everywhere (tests run on Linux).
- Real transcript facts this plan relies on (verified 2026-07-30 against `~/.claude/projects/`):
  - Transcript lines are JSON objects. Message lines have top-level `type` (`"user"` | `"assistant"`), `timestamp` (ISO, `Z` suffix), `isSidechain` (bool), `gitBranch`, `cwd`, and `message` (`{"role": ..., "content": ...}`). User `content` is a string; assistant `content` is a list of blocks (`{"type": "text", "text": ...}` among `thinking`/`tool_use` blocks). Non-message lines (`"type": "mode"`, `"file-history-snapshot"`, hook attachments) must be ignored.
  - Project dir munging: every character of the cwd not in `[A-Za-z0-9]` becomes `-` (e.g. `/Users/x/Documents/openworker` → `-Users-x-Documents-openworker`).
  - Interactive CLI sessions show in `ps` with command starting `claude` and a real tty (`ttys000`). IDE/ACP-spawned claudes show tty `??` — not injectable, must be filtered out.
  - `ps -o lstart=` is locale-dependent (this machine prints Portuguese). Use `ps -o etime=` (`[[dd-]hh:]mm:ss`, locale-independent) for process age.
  - `lsof -a -p PID -d cwd -Fn` prints `p<pid>` / `fcwd` / `n<path>` lines; the cwd is the line starting with `n`.

---

### Task 1: Transcript reader (`coworker/claude_bridge/transcript.py`)

**Files:**
- Create: `coworker/claude_bridge/__init__.py`
- Create: `coworker/claude_bridge/transcript.py`
- Test: `tests/test_claude_bridge_transcript.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces (used by Tasks 2 and 4):
  - `@dataclass Entry: role: str; text: str; timestamp: datetime | None`
  - `parse_line(line: str) -> Entry | None`
  - `tail(path: Path, n: int = 20) -> list[Entry]`
  - `last_branch(path: Path) -> str | None`
  - `wait_for_reply(path: Path, after: datetime, *, timeout: float = 120.0, poll: float = 1.0, settle: float = 5.0, sleep=time.sleep, clock=time.monotonic) -> str | None`

- [ ] **Step 1: Create the package init**

`coworker/claude_bridge/__init__.py`:

```python
"""Bridge to live Claude Code CLI sessions on this machine.

Domain package: discovery (which sessions are alive), transcript (what they said),
terminal (how to type into them). No dependency on coworker.tools or the engine —
the tool adapter in coworker/tools/claude_sessions.py wires these together.
"""
```

- [ ] **Step 2: Write the failing tests**

`tests/test_claude_bridge_transcript.py`:

```python
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
        path, after, timeout=60.0, poll=1.0, settle=5.0,
        sleep=sleep_and_reply, clock=fake.clock,
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
        path, after, timeout=3.0, poll=1.0, settle=60.0,
        sleep=fake.sleep, clock=fake.clock,
    )
    assert reply == "partial"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_transcript.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'coworker.claude_bridge.transcript'`

- [ ] **Step 4: Implement `transcript.py`**

```python
"""Read and watch a Claude Code session transcript (~/.claude/projects/*/*.jsonl).

Each line is one JSON object. Only `type: user|assistant` lines with visible text count
as entries; mode markers, snapshots, sidechain (subagent) lines, thinking-only and
tool-result-only lines are skipped. A truncated line (a write in progress) is skipped
too — parsing never raises. Files are re-read whole on each call: transcripts are a few
MB at worst and this keeps the reader stateless.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Entry:
    role: str  # "user" | "assistant"
    text: str
    timestamp: datetime | None


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _block_text(content: object) -> str:
    """Visible text of a message: user content is a string, assistant content a block
    list where only `text` blocks are human-directed (thinking/tool_use are not)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def parse_line(line: str) -> Entry | None:
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("isSidechain"):
        return None
    kind = data.get("type")
    if kind not in ("user", "assistant"):
        return None
    message = data.get("message")
    if not isinstance(message, dict):
        return None
    text = _block_text(message.get("content"))
    if not text.strip():
        return None
    return Entry(role=kind, text=text, timestamp=_parse_timestamp(data.get("timestamp")))


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def tail(path: Path, n: int = 20) -> list[Entry]:
    entries = [e for e in (parse_line(line) for line in _lines(path)) if e]
    return entries[-n:]


def last_branch(path: Path) -> str | None:
    """The session's git branch, from the newest line that recorded one."""
    branch: str | None = None
    for line in _lines(path):
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("gitBranch"), str):
            branch = data["gitBranch"]
    return branch


def wait_for_reply(
    path: Path,
    after: datetime,
    *,
    timeout: float = 120.0,
    poll: float = 1.0,
    settle: float = 5.0,
    sleep=time.sleep,
    clock=time.monotonic,
) -> str | None:
    """Assistant text newer than `after`, once the transcript has been quiet.

    A turn can emit several assistant entries (narration between tool calls), so a reply
    only counts as final after `settle` seconds with no new entry. On timeout, whatever
    arrived is returned anyway (partial beats silence); None means nothing arrived.
    """
    deadline = clock() + timeout
    seen = 0
    quiet_since: float | None = None
    texts: list[str] = []
    while True:
        texts = [
            e.text
            for e in tail(path, 50)
            if e.role == "assistant" and e.timestamp and e.timestamp > after
        ]
        if texts:
            if len(texts) != seen:
                seen = len(texts)
                quiet_since = clock()
            elif quiet_since is not None and clock() - quiet_since >= settle:
                return "\n\n".join(texts)
        if clock() >= deadline:
            return "\n\n".join(texts) if texts else None
        sleep(poll)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_transcript.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add coworker/claude_bridge/ tests/test_claude_bridge_transcript.py
git commit -m "Read a Claude Code session transcript for the bridge"
```

---

### Task 2: Session discovery (`models.py` + `discovery.py`)

**Files:**
- Create: `coworker/claude_bridge/models.py`
- Create: `coworker/claude_bridge/discovery.py`
- Test: `tests/test_claude_bridge_discovery.py`

**Interfaces:**
- Consumes (Task 1): `transcript.tail(path, n)`, `transcript.last_branch(path)`, `Entry`.
- Produces (used by Task 4):
  - `@dataclass LiveSession: pid: int; tty: str; cwd: str; branch: str | None; transcript: Path | None; transcript_confidence: str; last_activity: datetime | None; tail: str` with `to_dict() -> dict`
  - `class SessionDiscovery` with `__init__(self, *, run=subprocess.run, projects_dir: Path | None = None, own_pid: int | None = None, now=None)` and `list(self) -> list[LiveSession]`
  - `munge_cwd(cwd: str) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_claude_bridge_discovery.py`:

```python
"""Session discovery — fake `ps`/`lsof` runner, real transcript files in tmp_path."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from coworker.claude_bridge.discovery import SessionDiscovery, munge_cwd

NOW = datetime(2026, 7, 30, 12, 0, 0, tzinfo=timezone.utc)


class FakeRunner:
    """Answers ps/lsof invocations from a canned table; records calls."""

    def __init__(self, ps: str, cwds: dict[int, str], etimes: dict[int, str]):
        self.ps = ps
        self.cwds = cwds
        self.etimes = etimes
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        assert kwargs.get("timeout") == 10, "external calls must carry timeout=10"
        if cmd[0] == "ps" and "-axo" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=self.ps, stderr="")
        if cmd[0] == "ps" and "etime=" in " ".join(cmd):
            pid = int(cmd[-1])
            return subprocess.CompletedProcess(
                cmd, 0, stdout=self.etimes.get(pid, "01:00") + "\n", stderr=""
            )
        if cmd[0] == "lsof":
            pid = int(cmd[cmd.index("-p") + 1])
            cwd = self.cwds.get(pid)
            out = f"p{pid}\nfcwd\nn{cwd}\n" if cwd else ""
            return subprocess.CompletedProcess(cmd, 0 if cwd else 1, stdout=out, stderr="")
        raise AssertionError(f"unexpected command: {cmd}")


def _transcript_line(text: str, ts: str, branch: str = "main") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "gitBranch": branch,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }
    )


def _make_project(projects: Path, cwd: str, name: str, text: str, mtime: float) -> Path:
    proj = projects / munge_cwd(cwd)
    proj.mkdir(parents=True, exist_ok=True)
    f = proj / name
    f.write_text(
        _transcript_line(text, "2026-07-30T11:59:00.000Z", branch="fix/webhook") + "\n",
        encoding="utf-8",
    )
    import os

    os.utime(f, (mtime, mtime))
    return f


PS = (
    "  910   870 ttys000  claude --resume\n"
    " 2100  2050 ??       claude acp-mode\n"  # IDE session: no tty, must be skipped
    " 3300   001 ttys001  vim notes.txt\n"  # not claude
    " 4400   910 ttys000  node helper\n"  # not claude
)


def test_munge_cwd():
    assert munge_cwd("/Users/x/Documents/open_worker.app") == (
        "-Users-x-Documents-open-worker-app"
    )


def test_list_finds_interactive_claude_sessions(tmp_path: Path):
    projects = tmp_path / "projects"
    cwd = "/Users/x/dev/webhook"
    _make_project(projects, cwd, "aaa.jsonl", "fixing the webhook", NOW.timestamp() - 60)
    runner = FakeRunner(PS, cwds={910: cwd}, etimes={910: "02:00"})  # started 12:00 ago... 2 min
    disc = SessionDiscovery(
        run=runner, projects_dir=projects, own_pid=99999, now=lambda: NOW
    )
    sessions = disc.list()
    assert len(sessions) == 1
    s = sessions[0]
    assert (s.pid, s.tty, s.cwd) == (910, "ttys000", cwd)
    assert s.branch == "fix/webhook"
    assert s.transcript is not None and s.transcript.name == "aaa.jsonl"
    assert s.transcript_confidence == "matched"
    assert "fixing the webhook" in s.tail
    assert s.last_activity is not None


def test_list_excludes_own_process_tree(tmp_path: Path):
    # claude pid 910 is a child of own_pid 870 → the bridge must not see itself
    runner = FakeRunner(PS, cwds={910: "/w"}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner, projects_dir=tmp_path, own_pid=870, now=lambda: NOW
    )
    assert disc.list() == []


def test_list_session_without_transcript_still_listed(tmp_path: Path):
    runner = FakeRunner(PS, cwds={910: "/Users/x/dev/fresh"}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner, projects_dir=tmp_path, own_pid=99999, now=lambda: NOW
    )
    sessions = disc.list()
    assert len(sessions) == 1
    assert sessions[0].transcript is None
    assert sessions[0].transcript_confidence == "none"
    assert sessions[0].tail == ""


def test_list_marks_ambiguous_transcript_as_guessed(tmp_path: Path):
    projects = tmp_path / "projects"
    cwd = "/Users/x/dev/webhook"
    _make_project(projects, cwd, "old.jsonl", "older", NOW.timestamp() - 30)
    _make_project(projects, cwd, "new.jsonl", "newer", NOW.timestamp() - 10)
    runner = FakeRunner(PS, cwds={910: cwd}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner, projects_dir=projects, own_pid=99999, now=lambda: NOW
    )
    s = disc.list()[0]
    assert s.transcript is not None and s.transcript.name == "new.jsonl"
    assert s.transcript_confidence == "guessed"


def test_list_survives_ps_failure(tmp_path: Path):
    def broken(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)

    disc = SessionDiscovery(
        run=broken, projects_dir=tmp_path, own_pid=99999, now=lambda: NOW
    )
    assert disc.list() == []


def test_to_dict_serialises_for_the_model(tmp_path: Path):
    projects = tmp_path / "projects"
    cwd = "/Users/x/dev/webhook"
    _make_project(projects, cwd, "aaa.jsonl", "hi", NOW.timestamp() - 60)
    runner = FakeRunner(PS, cwds={910: cwd}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner, projects_dir=projects, own_pid=99999, now=lambda: NOW
    )
    d = disc.list()[0].to_dict()
    assert d["project"] == "webhook"
    assert d["tty"] == "ttys000"
    assert "transcript" not in d  # tty is the handle; paths stay out of the model's view
    assert isinstance(d["last_activity"], str)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `models.py`**

```python
"""LiveSession — one live Claude Code CLI session, as discovery sees it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class LiveSession:
    pid: int
    tty: str  # e.g. "ttys004" — the handle the tools pass around
    cwd: str
    branch: str | None
    transcript: Path | None
    transcript_confidence: str  # "matched" | "guessed" | "none"
    last_activity: datetime | None
    tail: str  # last few messages, summarised, for free-form matching

    def to_dict(self) -> dict:
        """Model-facing shape: the tty is the handle; filesystem paths stay internal."""
        return {
            "pid": self.pid,
            "tty": self.tty,
            "project": Path(self.cwd).name,
            "cwd": self.cwd,
            "branch": self.branch,
            "transcript_confidence": self.transcript_confidence,
            "last_activity": (
                self.last_activity.isoformat() if self.last_activity else None
            ),
            "tail": self.tail,
        }
```

- [ ] **Step 4: Implement `discovery.py`**

```python
"""Find live Claude Code CLI sessions: `ps` for processes, `lsof` for the cwd, then the
cwd munged into ~/.claude/projects/<dir>/ to locate the transcript.

Heuristics, documented: the transcript pick is "the newest .jsonl modified after the
process started" — exact when one file qualifies (confidence "matched"), a guess when
several or none do ("guessed"). Sessions with tty "??" (IDE/ACP-spawned) are skipped:
with no terminal there is nothing to type into. `ps -o lstart=` is locale-dependent, so
process age comes from `ps -o etime=` instead.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .models import LiveSession
from .transcript import last_branch, tail

# Slack applied to "modified after process start": mtimes and etime are second-granular.
_START_SLACK = timedelta(seconds=60)
_TAIL_MESSAGES = 3
_TAIL_CHARS = 200


def munge_cwd(cwd: str) -> str:
    """Claude Code's project-dir name: every char outside [A-Za-z0-9] becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _parse_etime(raw: str) -> timedelta | None:
    """`[[dd-]hh:]mm:ss` → timedelta. "02:00" = 2 minutes; "1-03:00:00" = 27h."""
    raw = raw.strip()
    if not raw:
        return None
    days = 0
    if "-" in raw:
        day_part, raw = raw.split("-", 1)
        try:
            days = int(day_part)
        except ValueError:
            return None
    parts = raw.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        hours, (minutes, seconds) = 0, nums
    elif len(nums) == 3:
        hours, minutes, seconds = nums
    else:
        return None
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)


def _is_claude(command: str) -> bool:
    first = command.split()[0] if command.split() else ""
    return Path(first).name == "claude"


class SessionDiscovery:
    def __init__(
        self,
        *,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        projects_dir: Path | None = None,
        own_pid: int | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._run = run
        self._projects = projects_dir or Path.home() / ".claude" / "projects"
        self._own_pid = os.getpid() if own_pid is None else own_pid
        self._now = now or (lambda: datetime.now(timezone.utc))

    def list(self) -> list[LiveSession]:
        procs = self._processes()
        own = self._own_tree(procs)
        sessions: list[LiveSession] = []
        for pid, ppid, tty, command in procs:
            if not _is_claude(command) or tty in ("??", "-", "") or pid in own:
                continue
            cwd = self._cwd(pid)
            if not cwd:
                continue
            transcript, confidence = self._pick_transcript(cwd, self._started(pid))
            branch = last_branch(transcript) if transcript else None
            entries = tail(transcript, _TAIL_MESSAGES) if transcript else []
            summary = "\n".join(
                f"{e.role}: {e.text[:_TAIL_CHARS]}" for e in entries
            )
            last_activity = None
            if transcript is not None:
                try:
                    last_activity = datetime.fromtimestamp(
                        transcript.stat().st_mtime, tz=timezone.utc
                    )
                except OSError:
                    pass
            sessions.append(
                LiveSession(
                    pid=pid,
                    tty=tty,
                    cwd=cwd,
                    branch=branch,
                    transcript=transcript,
                    transcript_confidence=confidence,
                    last_activity=last_activity,
                    tail=summary,
                )
            )
        return sessions

    # -- process table ---------------------------------------------------------

    def _processes(self) -> list[tuple[int, int, str, str]]:
        out = self._exec(["ps", "-axo", "pid=,ppid=,tty=,command="])
        procs: list[tuple[int, int, str, str]] = []
        for line in (out or "").splitlines():
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            try:
                procs.append((int(parts[0]), int(parts[1]), parts[2], parts[3]))
            except ValueError:
                continue
        return procs

    def _own_tree(self, procs: list[tuple[int, int, str, str]]) -> set[int]:
        """Own pid + all descendants — the agent must never target the session that is
        answering it (a claude spawned via run_shell would be a child of this process)."""
        children: dict[int, list[int]] = {}
        for pid, ppid, _tty, _cmd in procs:
            children.setdefault(ppid, []).append(pid)
        tree = {self._own_pid}
        queue = [self._own_pid]
        while queue:
            for child in children.get(queue.pop(), []):
                if child not in tree:
                    tree.add(child)
                    queue.append(child)
        return tree

    def _cwd(self, pid: int) -> str | None:
        out = self._exec(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
        for line in (out or "").splitlines():
            if line.startswith("n"):
                return line[1:]
        return None

    def _started(self, pid: int) -> datetime | None:
        out = self._exec(["ps", "-o", "etime=", "-p", str(pid)])
        elapsed = _parse_etime(out or "")
        return self._now() - elapsed if elapsed else None

    def _pick_transcript(
        self, cwd: str, started: datetime | None
    ) -> tuple[Path | None, str]:
        project = self._projects / munge_cwd(cwd)
        try:
            candidates = sorted(
                project.glob("*.jsonl"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            candidates = []
        if not candidates:
            return None, "none"
        if started is not None:
            floor = (started - _START_SLACK).timestamp()
            recent = [f for f in candidates if f.stat().st_mtime >= floor]
            if len(recent) == 1:
                return recent[0], "matched"
            if recent:
                return recent[0], "guessed"
        return candidates[0], "guessed"

    def _exec(self, cmd: list[str]) -> str | None:
        try:
            out = self._run(cmd, capture_output=True, text=True, timeout=10)
        except Exception:
            return None
        return out.stdout if out.returncode == 0 else None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_discovery.py tests/test_claude_bridge_transcript.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add coworker/claude_bridge/models.py coworker/claude_bridge/discovery.py tests/test_claude_bridge_discovery.py
git commit -m "Discover live Claude Code CLI sessions on this machine"
```

---

### Task 3: Terminal driver (`coworker/claude_bridge/terminal.py`)

**Files:**
- Create: `coworker/claude_bridge/terminal.py`
- Test: `tests/test_claude_bridge_terminal.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (stdlib only).
- Produces (used by Task 4):
  - `class TerminalDriver(Protocol)` with `find_target(self, tty: str) -> str | None` and `send_text(self, target: str, text: str) -> bool`
  - `class ITerm2Driver` implementing it, `__init__(self, *, run=subprocess.run)`

- [ ] **Step 1: Write the failing tests**

`tests/test_claude_bridge_terminal.py`:

```python
"""ITerm2Driver — fake osascript runner; asserts on the generated AppleScript."""

from __future__ import annotations

import subprocess

from coworker.claude_bridge.terminal import ITerm2Driver


class FakeOsascript:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
        self.scripts: list[str] = []

    def __call__(self, cmd, **kwargs):
        assert cmd[0] == "osascript" and cmd[1] == "-e"
        assert kwargs.get("timeout") == 10
        self.scripts.append(cmd[2])
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout=self.stdout, stderr=""
        )


def test_find_target_matches_tty_with_dev_prefix():
    fake = FakeOsascript(stdout="w0t0p0:ABC\n")
    driver = ITerm2Driver(run=fake)
    assert driver.find_target("ttys004") == "w0t0p0:ABC"
    assert '"/dev/ttys004"' in fake.scripts[0]


def test_find_target_none_when_no_match_or_failure():
    assert ITerm2Driver(run=FakeOsascript(stdout="")).find_target("ttys004") is None
    assert (
        ITerm2Driver(run=FakeOsascript(stdout="x", returncode=1)).find_target("ttys004")
        is None
    )


def test_find_target_survives_osascript_crash():
    def broken(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)

    assert ITerm2Driver(run=broken).find_target("ttys004") is None


def test_send_text_escapes_and_collapses():
    fake = FakeOsascript(stdout="ok\n")
    driver = ITerm2Driver(run=fake)
    assert driver.send_text("w0t0p0:ABC", 'say "hi"\\now\nplease') is True
    script = fake.scripts[0]
    assert '\\"hi\\"' in script  # quotes escaped
    assert "\\\\now" in script  # literal backslash doubled
    assert 'write text "say \\"hi\\"\\\\now please"' in script  # one line, fully escaped


def test_send_text_multiline_becomes_single_line():
    fake = FakeOsascript(stdout="ok\n")
    ITerm2Driver(run=fake).send_text("id", "line one\nline two")
    assert 'write text "line one line two"' in fake.scripts[0]


def test_send_text_false_on_missing_session_or_failure():
    assert ITerm2Driver(run=FakeOsascript(stdout="")).send_text("id", "x") is False
    assert (
        ITerm2Driver(run=FakeOsascript(stdout="ok", returncode=1)).send_text("id", "x")
        is False
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_terminal.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `terminal.py`**

```python
"""Type into a terminal tab. TerminalDriver is the seam: iTerm2 today, Terminal.app or
tmux later, without the tool layer changing.

Contract every driver honours: `find_target` returns None for "not found" (tab closed,
app not running) and `send_text` returns False on failure — neither ever raises for an
absent target. iTerm2 is driven via `osascript`; `write text` appends Enter, so
multiline input is collapsed to one line (a literal newline would submit each line as a
separate prompt).
"""

from __future__ import annotations

import subprocess
from typing import Callable, Protocol


class TerminalDriver(Protocol):
    def find_target(self, tty: str) -> str | None: ...

    def send_text(self, target: str, text: str) -> bool: ...


_FIND = """\
tell application "iTerm2"
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if tty of s is "{tty}" then return id of s
      end repeat
    end repeat
  end repeat
end tell
return ""
"""

_SEND = """\
tell application "iTerm2"
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if id of s is "{target}" then
          tell s to write text "{text}"
          return "ok"
        end if
      end repeat
    end repeat
  end repeat
end tell
return ""
"""


def _escape(text: str) -> str:
    """Into an AppleScript string literal: backslashes first, then quotes; newlines
    collapse to spaces (write text sends Enter — a newline would submit early)."""
    flat = " ".join(text.splitlines())
    return flat.replace("\\", "\\\\").replace('"', '\\"')


class ITerm2Driver:
    def __init__(
        self, *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    ) -> None:
        self._run = run

    def find_target(self, tty: str) -> str | None:
        dev = tty if tty.startswith("/dev/") else f"/dev/{tty}"
        out = self._osascript(_FIND.format(tty=_escape(dev)))
        return out or None

    def send_text(self, target: str, text: str) -> bool:
        script = _SEND.format(target=_escape(target), text=_escape(text))
        return self._osascript(script) == "ok"

    def _osascript(self, script: str) -> str | None:
        try:
            out = self._run(
                ["osascript", "-e", script], capture_output=True, text=True, timeout=10
            )
        except Exception:
            return None
        if out.returncode != 0:
            return None
        return out.stdout.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_claude_bridge_terminal.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add coworker/claude_bridge/terminal.py tests/test_claude_bridge_terminal.py
git commit -m "Drive an iTerm2 tab behind a terminal-driver seam"
```

---

### Task 4: Tool adapter (`coworker/tools/claude_sessions.py`)

**Files:**
- Create: `coworker/tools/claude_sessions.py`
- Test: `tests/test_claude_session_tools.py`

**Interfaces:**
- Consumes (Tasks 1–3): `SessionDiscovery.list()`, `LiveSession`, `TerminalDriver`, `transcript.tail`, `transcript.wait_for_reply`.
- Produces (used by Task 5):
  - `claude_session_tools(discovery, driver, *, waiter=wait_for_reply, now=None) -> list` — three closures named `find_claude_sessions`, `read_claude_transcript`, `send_to_claude_session`, each with `__coworker_schema__` attached.
  - `claude_bridge_tools() -> list` — zero-arg factory wiring real `SessionDiscovery()` + `ITerm2Driver()`; what `agent.py` calls.

- [ ] **Step 1: Write the failing tests**

`tests/test_claude_session_tools.py`:

```python
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
    t = _tools(FakeDiscovery([_session(transcript=_write_transcript(tmp_path, ["hi"]))]), FakeDriver())
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
        {"role": "assistant", "text": "second", "timestamp": "2026-07-30T12:00:11+00:00"}
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `source .venv/bin/activate && pytest tests/test_claude_session_tools.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `tools/claude_sessions.py`**

```python
"""Bridge tools — find live Claude Code CLI sessions, read their transcripts, send them
input. Thin adapter over coworker.claude_bridge (same factory-of-closures shape as
git_tools); all logic lives in the domain package, all deps are injected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from ..claude_bridge.discovery import SessionDiscovery
from ..claude_bridge.models import LiveSession
from ..claude_bridge.terminal import ITerm2Driver, TerminalDriver
from ..claude_bridge.transcript import tail as transcript_tail
from ..claude_bridge.transcript import wait_for_reply

_FIND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "find_claude_sessions",
        "description": (
            "List the live Claude Code CLI sessions in terminal tabs on this machine: "
            "project, branch, last activity, and a tail of each session's transcript. "
            "Use the tail to decide which session the user means, then address it by "
            "its `tty` in the other claude-session tools."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_claude_transcript",
        "description": (
            "Recent messages of one live Claude Code session (by `tty` from "
            "find_claude_sessions). Use it to answer whether the session finished, "
            "what it did, or whether it hit an error. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tty": {"type": "string", "description": "The session's tty."},
                "n": {
                    "type": "integer",
                    "description": "How many recent messages (default 20).",
                },
            },
            "required": ["tty"],
        },
    },
}

_SEND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "send_to_claude_session",
        "description": (
            "Type a message into a live Claude Code session's terminal tab (by `tty` "
            "from find_claude_sessions) and wait for its reply. The reply may take a "
            "while; on timeout you get the session's latest entries instead."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tty": {"type": "string", "description": "The session's tty."},
                "text": {"type": "string", "description": "The message to send."},
                "wait_seconds": {
                    "type": "integer",
                    "description": "How long to wait for the reply (default 120).",
                },
            },
            "required": ["tty", "text"],
        },
    },
}


def claude_session_tools(
    discovery: Any,
    driver: TerminalDriver,
    *,
    waiter: Callable[..., Optional[str]] = wait_for_reply,
    now: Callable[[], datetime] | None = None,
) -> list:
    clock = now or (lambda: datetime.now(timezone.utc))

    def _by_tty(tty: str) -> LiveSession | None:
        for session in discovery.list():
            if session.tty == tty:
                return session
        return None

    def find_claude_sessions() -> dict[str, Any]:
        sessions = discovery.list()
        result: dict[str, Any] = {"sessions": [s.to_dict() for s in sessions]}
        if not sessions:
            result["hint"] = (
                "No live Claude Code CLI session found in any terminal tab. "
                "IDE-embedded sessions have no terminal and cannot be reached."
            )
        return result

    def read_claude_transcript(tty: str, n: int = 20) -> dict[str, Any]:
        session = _by_tty(tty)
        if session is None:
            return {"error": "session_gone"}
        if session.transcript is None:
            return {"error": "no_transcript"}
        count = n if isinstance(n, int) and n > 0 else 20
        return {
            "entries": [
                {
                    "role": e.role,
                    "text": e.text,
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                }
                for e in transcript_tail(session.transcript, count)
            ]
        }

    def send_to_claude_session(
        tty: str, text: str, wait_seconds: int = 120
    ) -> dict[str, Any]:
        session = _by_tty(tty)
        if session is None:
            return {"error": "session_gone"}
        target = driver.find_target(session.tty)
        if target is None:
            return {"error": "session_gone"}
        after = clock()
        if not driver.send_text(target, text):
            return {"error": "send_failed"}
        if session.transcript is None:
            return {"status": "sent_no_reply", "last_entries": []}
        wait = wait_seconds if isinstance(wait_seconds, int) and wait_seconds > 0 else 120
        reply = waiter(session.transcript, after, timeout=float(wait))
        if reply is not None:
            return {"status": "replied", "reply": reply}
        return {
            "status": "sent_no_reply",
            "last_entries": [e.text for e in transcript_tail(session.transcript, 5)],
        }

    find_claude_sessions.__coworker_schema__ = _FIND_SCHEMA
    read_claude_transcript.__coworker_schema__ = _READ_SCHEMA
    send_to_claude_session.__coworker_schema__ = _SEND_SCHEMA
    return [find_claude_sessions, read_claude_transcript, send_to_claude_session]


def claude_bridge_tools() -> list:
    """The production wiring: real discovery, real iTerm2 driver."""
    return claude_session_tools(SessionDiscovery(), ITerm2Driver())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `source .venv/bin/activate && pytest tests/test_claude_session_tools.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add coworker/tools/claude_sessions.py tests/test_claude_session_tools.py
git commit -m "Expose the Claude Code bridge as three agent tools"
```

---

### Task 5: Registration in `agent.py` + full-suite check

**Files:**
- Modify: `coworker/agent.py` (imports near line 14; registration after the selfwake block near line 241)
- Test: append one test to `tests/test_claude_session_tools.py`

**Interfaces:**
- Consumes (Task 4): `claude_bridge_tools()`.
- Produces: messaging personas (the Assistant answering WhatsApp) get the three tools on macOS.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_claude_session_tools.py`:

```python
def test_agent_module_gates_bridge_on_macos():
    # Registration wiring: agent.py must reference the factory and the darwin gate.
    import inspect

    import coworker.agent as agent_module

    source = inspect.getsource(agent_module)
    assert "claude_bridge_tools" in source
    assert 'sys.platform == "darwin"' in source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source .venv/bin/activate && pytest tests/test_claude_session_tools.py::test_agent_module_gates_bridge_on_macos -v`
Expected: FAIL on the first assert

- [ ] **Step 3: Wire into `agent.py`**

Add to the imports (top of file, alongside the existing ones):

```python
import sys
```

and with the other tool imports:

```python
from .tools.claude_sessions import claude_bridge_tools
```

Insert after the selfwake block (currently lines 238–241, right before `instructions = ...`):

```python
    # Claude Code bridge (macOS): messaging personas can locate live Claude Code CLI
    # sessions in terminal tabs, read their transcripts, and type into them — the
    # WhatsApp → local-session relay. Injection is AppleScript, so Darwin only.
    if agent.messaging and sys.platform == "darwin":
        registry.register_all(claude_bridge_tools())
```

- [ ] **Step 4: Run the new test, then the full suite**

Run: `source .venv/bin/activate && pytest tests/test_claude_session_tools.py -v && pytest`
Expected: new test PASS; full suite green except pre-existing known failures (see `TESTE-MODULOS.md`; `Sidebar.test.tsx` failures are frontend and pre-existing).

- [ ] **Step 5: Manual smoke check (the only step touching the real machine)**

```bash
source .venv/bin/activate && python -c "
from coworker.tools.claude_sessions import claude_bridge_tools
find = claude_bridge_tools()[0]
import json; print(json.dumps(find(), indent=2, ensure_ascii=False))
"
```

Expected: this very session (and any other live `claude` CLI) listed with project, branch, tail; IDE sessions absent.

- [ ] **Step 6: Commit**

```bash
git add coworker/agent.py tests/test_claude_session_tools.py
git commit -m "Give messaging personas the Claude Code bridge on macOS"
```

---

## Out of scope (explicitly)

- Claude Code hooks for push-based status (spec: possible phase 2).
- Terminal.app / tmux drivers (the `TerminalDriver` seam exists for them).
- Any change to WhatsApp routing, session-per-contact, or the reply path — already shipped (#23, #24, #29).
