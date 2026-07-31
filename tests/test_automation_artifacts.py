"""Artifact collection must stay inside the task's workspace — and must never be able to
walk the whole filesystem.

Real incident (2026-07-31): a reminder created from a WhatsApp conversation by the
`assistant` persona — which HAS no workspace — got `workspace="/"`. Each run sent its
message in ~2s and then spent 20 minutes to 2 hours in `rglob("*")` over the entire disk
looking for "artifacts", collecting `~/Library/Preferences`, the login keychain and other
projects' files. The run only recorded `finished_at` after that walk, so it sat marked
`running`, inbound WhatsApp messages queued behind it, and were then re-delivered in a
burst — which is how a delete request got applied to the wrong reminder.
"""

import time

from coworker.server.manager import _recent_files


def test_collects_files_written_during_the_run(tmp_path):
    import os

    old = tmp_path / "old.txt"
    old.write_text("before", encoding="utf-8")
    # Backdated explicitly: `_recent_files` allows a second of slack for coarse clocks,
    # so a file written in the same second as `started` legitimately counts as fresh.
    os.utime(old, (1000, 1000))
    started = time.time()
    fresh = tmp_path / "fresh.md"
    fresh.write_text("during", encoding="utf-8")

    got = _recent_files(str(tmp_path), since=started)

    assert "fresh.md" in got
    assert "old.txt" not in got


def test_a_workspaceless_task_collects_nothing_instead_of_walking_the_disk(tmp_path):
    # "/" and "" are what a persona without a workspace yields. Neither may be walked:
    # the scan is unbounded there and everything it finds belongs to the user, not the run.
    for root in ("/", "", "~"):
        assert _recent_files(root, since=0) == []


def test_the_scan_is_bounded_by_depth(tmp_path):
    # A workspace can still be large; the walk must not descend forever.
    deep = tmp_path
    for i in range(12):
        deep = deep / f"level{i}"
    deep.mkdir(parents=True)
    buried = deep / "buried.txt"
    buried.write_text("deep", encoding="utf-8")
    shallow = tmp_path / "shallow.txt"
    shallow.write_text("near", encoding="utf-8")

    got = _recent_files(str(tmp_path), since=0)

    assert "shallow.txt" in got
    assert not any("buried" in p for p in got)


def test_hidden_directories_are_skipped(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (tmp_path / "kept.txt").write_text("x", encoding="utf-8")

    got = _recent_files(str(tmp_path), since=0)

    assert got == ["kept.txt"]


def test_the_result_is_capped(tmp_path):
    for i in range(40):
        (tmp_path / f"f{i}.txt").write_text("x", encoding="utf-8")
    assert len(_recent_files(str(tmp_path), since=0, limit=5)) == 5


def test_a_missing_workspace_is_not_an_error(tmp_path):
    assert _recent_files(str(tmp_path / "nope"), since=0) == []
