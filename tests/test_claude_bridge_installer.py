"""Installer — merge into settings.json, never clobber; idempotent; clean uninstall."""

from __future__ import annotations

import json
from pathlib import Path

from coworker.claude_bridge.install import install, uninstall

EVENTS = ["Stop", "Notification", "SessionEnd"]


def _settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fresh_install_registers_three_hooks(tmp_path: Path):
    settings = tmp_path / "settings.json"
    bridge = tmp_path / "ow-bridge"
    events = install(settings, bridge, python="/usr/bin/python3")
    assert sorted(events) == sorted(EVENTS)
    data = _settings(settings)
    for event in EVENTS:
        (entry,) = data["hooks"][event]
        (hook,) = entry["hooks"]
        assert hook["type"] == "command"
        assert "/usr/bin/python3" in hook["command"]
        assert str(bridge / "hook.py") in hook["command"]
    # the hook script was copied and is real python
    copied = (bridge / "hook.py").read_text(encoding="utf-8")
    assert "def run(" in copied


def test_install_merges_with_existing_user_hooks(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]
                },
            }
        ),
        encoding="utf-8",
    )
    install(settings, tmp_path / "ow-bridge", python="py")
    data = _settings(settings)
    assert data["model"] == "opus"  # untouched
    stop_cmds = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
    assert "say done" in stop_cmds  # user hook preserved
    assert any("ow-bridge/hook.py" in c for c in stop_cmds)  # ours added


def test_install_is_idempotent(tmp_path: Path):
    settings = tmp_path / "settings.json"
    bridge = tmp_path / "ow-bridge"
    install(settings, bridge, python="py")
    once = _settings(settings)
    install(settings, bridge, python="py")
    assert _settings(settings) == once


def test_install_creates_backup_once(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    install(settings, tmp_path / "ow-bridge", python="py")
    backup = tmp_path / "settings.json.ow-backup"
    assert json.loads(backup.read_text(encoding="utf-8")) == {"model": "opus"}
    # a second install must not overwrite the original backup
    install(settings, tmp_path / "ow-bridge", python="py")
    assert json.loads(backup.read_text(encoding="utf-8")) == {"model": "opus"}


def test_uninstall_removes_only_ours(tmp_path: Path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]
                }
            }
        ),
        encoding="utf-8",
    )
    bridge = tmp_path / "ow-bridge"
    install(settings, bridge, python="py")
    removed = uninstall(settings, bridge)
    assert sorted(removed) == sorted(EVENTS)
    data = _settings(settings)
    stop_cmds = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
    assert stop_cmds == ["say done"]  # user hook survives
    for event in ("Notification", "SessionEnd"):
        assert event not in data["hooks"]  # emptied lists are dropped


def test_uninstall_when_never_installed(tmp_path: Path):
    settings = tmp_path / "settings.json"
    assert uninstall(settings, tmp_path / "ow-bridge") == []
