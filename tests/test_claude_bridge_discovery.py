"""Session discovery — fake `ps`/`lsof` runner, real transcript files in tmp_path."""

from __future__ import annotations

import json
import os
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
            return subprocess.CompletedProcess(
                cmd, 0 if cwd else 1, stdout=out, stderr=""
            )
        raise AssertionError(f"unexpected command: {cmd}")


def _transcript_line(text: str, ts: str, branch: str = "main") -> str:
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": ts,
            "gitBranch": branch,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
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
    runner = FakeRunner(PS, cwds={910: cwd}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner,
        projects_dir=projects,
        own_pid=99999,
        now=lambda: NOW,
        bridge_dir=tmp_path / "nobridge",
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
        run=runner,
        projects_dir=tmp_path,
        own_pid=870,
        now=lambda: NOW,
        bridge_dir=tmp_path / "nobridge",
    )
    assert disc.list() == []


def test_list_session_without_transcript_still_listed(tmp_path: Path):
    runner = FakeRunner(PS, cwds={910: "/Users/x/dev/fresh"}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner,
        projects_dir=tmp_path,
        own_pid=99999,
        now=lambda: NOW,
        bridge_dir=tmp_path / "nobridge",
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
        run=runner,
        projects_dir=projects,
        own_pid=99999,
        now=lambda: NOW,
        bridge_dir=tmp_path / "nobridge",
    )
    s = disc.list()[0]
    assert s.transcript is not None and s.transcript.name == "new.jsonl"
    assert s.transcript_confidence == "guessed"


def test_list_survives_ps_failure(tmp_path: Path):
    def broken(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)

    disc = SessionDiscovery(
        run=broken,
        projects_dir=tmp_path,
        own_pid=99999,
        now=lambda: NOW,
        bridge_dir=tmp_path / "nobridge",
    )
    assert disc.list() == []


def test_to_dict_serialises_for_the_model(tmp_path: Path):
    projects = tmp_path / "projects"
    cwd = "/Users/x/dev/webhook"
    _make_project(projects, cwd, "aaa.jsonl", "hi", NOW.timestamp() - 60)
    runner = FakeRunner(PS, cwds={910: cwd}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner,
        projects_dir=projects,
        own_pid=99999,
        now=lambda: NOW,
        bridge_dir=tmp_path / "nobridge",
    )
    d = disc.list()[0].to_dict()
    assert d["project"] == "webhook"
    assert d["tty"] == "ttys000"
    # tty is the handle; filesystem paths stay out of the model's view
    assert "transcript" not in d
    assert "cwd" not in d
    assert isinstance(d["last_activity"], str)


def _write_registry(
    bridge: Path,
    session_id: str,
    pid: int,
    transcript: Path,
    cwd: str,
    status: str = "idle",
    updated_at: str = "2026-07-30T11:59:30+00:00",
) -> None:
    sessions = bridge / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "transcript_path": str(transcript),
                "cwd": cwd,
                "pid": pid,
                "status": status,
                "message": None,
                "updated_at": updated_at,
            }
        ),
        encoding="utf-8",
    )


def test_registry_match_beats_mtime_heuristic(tmp_path: Path):
    projects = tmp_path / "projects"
    bridge = tmp_path / "bridge"
    cwd = "/Users/x/dev/webhook"
    # Two candidates: the mtime heuristic alone would guess the newer one...
    right = _make_project(
        projects, cwd, "right.jsonl", "the real one", NOW.timestamp() - 30
    )
    _make_project(projects, cwd, "decoy.jsonl", "newer decoy", NOW.timestamp() - 5)
    # ...but the registry says pid 910 is session "right".
    _write_registry(bridge, "right", 910, right, cwd)
    runner = FakeRunner(PS, cwds={910: cwd}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner,
        projects_dir=projects,
        own_pid=99999,
        now=lambda: NOW,
        bridge_dir=bridge,
    )
    (s,) = disc.list()
    assert s.session_id == "right"
    assert s.transcript is not None and s.transcript.name == "right.jsonl"
    assert s.transcript_confidence == "matched"
    assert s.status == "idle"
    d = s.to_dict()
    assert d["session_id"] == "right"
    assert d["status"] == "idle"


def test_registry_idle_but_newer_transcript_means_running(tmp_path: Path):
    projects = tmp_path / "projects"
    bridge = tmp_path / "bridge"
    cwd = "/Users/x/dev/webhook"
    t = _make_project(projects, cwd, "aaa.jsonl", "working...", NOW.timestamp() - 60)
    # registry Stop happened at 11:58; transcript moved at 11:59 → a new turn started
    _write_registry(
        bridge, "aaa", 910, t, cwd, status="idle",
        updated_at="2026-07-30T11:58:00+00:00",
    )
    os.utime(t, (NOW.timestamp() - 60, NOW.timestamp() - 60))
    runner = FakeRunner(PS, cwds={910: cwd}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner,
        projects_dir=projects,
        own_pid=99999,
        now=lambda: NOW,
        bridge_dir=bridge,
    )
    (s,) = disc.list()
    assert s.status == "running"


def test_no_registry_entry_falls_back_to_heuristic(tmp_path: Path):
    projects = tmp_path / "projects"
    cwd = "/Users/x/dev/webhook"
    _make_project(projects, cwd, "aaa.jsonl", "hi", NOW.timestamp() - 60)
    runner = FakeRunner(PS, cwds={910: cwd}, etimes={910: "02:00"})
    disc = SessionDiscovery(
        run=runner,
        projects_dir=projects,
        own_pid=99999,
        now=lambda: NOW,
        bridge_dir=tmp_path / "empty-bridge",
    )
    (s,) = disc.list()
    assert s.session_id is None
    assert s.status is None
    assert s.transcript is not None  # heuristic still worked
