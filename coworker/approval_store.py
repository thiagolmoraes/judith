"""Persistent "always allow" grants — the file behind permanent pre-approvals.

Session allowlists (`PermissionEngine.session_allow_*`) die with the session, so a
tool the user trusts re-prompts on every new session and every scheduled run. This
store holds the grants that should survive: blanket tool names, exact shell
commands, and per-tool target pins for external send tools (the spec's scope rule —
`PermissionEngine.grant_persistent` decides which scope a grant lands in; this
module only stores).

Reads are mtime-cached so an edit from the Settings screen (or by hand) applies to
live sessions without a restart. Writes are atomic (tmp + rename). A missing or
corrupt file reads as empty — never fails a permission check.

**Inviolable rule (same as `overrides.py`): user-local, NEVER written by a
persona/package.** Only the approval surfaces and the Settings screen write here.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path

# One lock per store file, shared across instances in this process — the server and
# the engines it hosts each build their own ApprovalStore over the same path, and an
# unsynchronized read-mutate-write pair could drop one writer's grant.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.Lock())


class ApprovalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._cache: dict = {}
        self._mtime: float | None = None
        self._lock = _lock_for(self.path)

    # -- reads -------------------------------------------------------------------
    def allow_tools(self) -> set[str]:
        return {str(t) for t in self._load().get("allow_tools", [])}

    def allow_commands(self) -> set[str]:
        return {str(c) for c in self._load().get("allow_commands", [])}

    def allow_targets(self) -> dict[str, set[str]]:
        raw = self._load().get("allow_targets", {})
        if not isinstance(raw, dict):
            return {}
        return {
            str(tool): {str(t) for t in targets}
            for tool, targets in raw.items()
            if isinstance(targets, list) and targets
        }

    def snapshot(self) -> dict:
        """JSON-shaped view for the server API (sorted for stable rendering)."""
        return {
            "allow_tools": sorted(self.allow_tools()),
            "allow_commands": sorted(self.allow_commands()),
            "allow_targets": {
                tool: sorted(targets)
                for tool, targets in sorted(self.allow_targets().items())
            },
        }

    # -- writes ------------------------------------------------------------------
    def grant_tool(self, name: str) -> None:
        self._mutate(lambda d: _add(d, "allow_tools", name))

    def grant_command(self, command: str) -> None:
        self._mutate(lambda d: _add(d, "allow_commands", command))

    def grant_target(self, tool: str, target: str) -> None:
        def apply(d: dict) -> None:
            targets = d.setdefault("allow_targets", {}).setdefault(tool, [])
            if target not in targets:
                targets.append(target)

        self._mutate(apply)

    def revoke_tool(self, name: str) -> None:
        self._mutate(lambda d: _remove(d, "allow_tools", name))

    def revoke_command(self, command: str) -> None:
        self._mutate(lambda d: _remove(d, "allow_commands", command))

    def revoke_target(self, tool: str, target: str) -> None:
        def apply(d: dict) -> None:
            targets = d.get("allow_targets", {}).get(tool, [])
            if target in targets:
                targets.remove(target)
            if not targets:
                d.get("allow_targets", {}).pop(tool, None)

        self._mutate(apply)

    # -- plumbing ----------------------------------------------------------------
    def _load(self) -> dict:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._cache, self._mtime = {}, None
            return self._cache
        if mtime == self._mtime:
            return self._cache
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self._cache = data if isinstance(data, dict) else {}
        self._mtime = mtime
        return self._cache

    def _mutate(self, apply) -> None:
        # Deep copy: `apply` mutates nested lists, and sharing them with `_cache`
        # would leak an unpersisted grant into reads if the write below fails.
        # The lock serializes the whole read-mutate-write cycle across the
        # process's instances over this file.
        with self._lock:
            self._mtime = None  # drop the cache: re-read the file inside the lock
            data = copy.deepcopy(self._load())
            data.setdefault("allow_tools", [])
            data.setdefault("allow_commands", [])
            data.setdefault("allow_targets", {})
            apply(data)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            self._cache = data
            try:
                self._mtime = self.path.stat().st_mtime
            except OSError:
                self._mtime = None


def _add(data: dict, key: str, value: str) -> None:
    values = data.setdefault(key, [])
    if value and value not in values:
        values.append(value)


def _remove(data: dict, key: str, value: str) -> None:
    values = data.get(key, [])
    if value in values:
        values.remove(value)
