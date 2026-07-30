"""Register the bridge hooks in ~/.claude/settings.json — merge, never clobber.

The hook script is COPIED to <bridge_dir>/hook.py and the copy is what gets
registered, so the hook keeps working if the repo moves or the package updates.
Our entries are identified by the "ow-bridge/hook.py" marker in the command:
install is idempotent, and uninstall removes exactly what install added.

Usage: python -m coworker.claude_bridge.install [--uninstall]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

_EVENTS = ("Stop", "Notification", "SessionEnd")
_MARKER = "ow-bridge/hook.py"


def _load(settings_path: Path) -> dict:
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _is_ours(entry: dict) -> bool:
    return any(
        _MARKER in (hook.get("command") or "")
        for hook in entry.get("hooks", [])
        if isinstance(hook, dict)
    )


def install(
    settings_path: Path, bridge_dir: Path, *, python: Optional[str] = None
) -> list[str]:
    bridge_dir.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name("hook_script.py")
    hook_path = bridge_dir / "hook.py"
    shutil.copyfile(source, hook_path)

    data = _load(settings_path)
    if settings_path.exists():
        backup = settings_path.with_name(settings_path.name + ".ow-backup")
        if not backup.exists():
            shutil.copyfile(settings_path, backup)

    interpreter = python or sys.executable
    command = f'"{interpreter}" "{hook_path}"'
    hooks = data.setdefault("hooks", {})
    registered: list[str] = []
    for event in _EVENTS:
        entries = hooks.setdefault(event, [])
        entries[:] = [e for e in entries if not (isinstance(e, dict) and _is_ours(e))]
        entries.append({"hooks": [{"type": "command", "command": command}]})
        registered.append(event)

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return registered


def uninstall(settings_path: Path, bridge_dir: Path) -> list[str]:
    data = _load(settings_path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []
    removed: list[str] = []
    for event in _EVENTS:
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not (isinstance(e, dict) and _is_ours(e))]
        if len(kept) != len(entries):
            removed.append(event)
        if kept:
            hooks[event] = kept
        else:
            hooks.pop(event, None)
    if removed:
        settings_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    try:
        (bridge_dir / "hook.py").unlink()
    except OSError:
        pass
    return removed


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m coworker.claude_bridge.install",
        description="Install the OpenWorker bridge hooks into Claude Code.",
    )
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    settings = Path.home() / ".claude" / "settings.json"
    bridge = Path.home() / ".claude" / "ow-bridge"
    if args.uninstall:
        removed = uninstall(settings, bridge)
        print(f"removed hooks: {', '.join(removed) or 'none'}")
    else:
        registered = install(settings, bridge)
        print(f"registered hooks: {', '.join(registered)}")
        print("restart open Claude Code sessions to pick them up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
