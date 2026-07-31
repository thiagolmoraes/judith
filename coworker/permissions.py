"""Permission engine — decides allow / deny / ask-user for each proposed tool call.

Modes: Plan (read-only) · Interactive (auto reads, ask on writes/commands) · Auto
(allow, still path-scoped). Refined by argument patterns (path-under-root, command
prefixes) and a session allowlist. The engine only *decides*; the turn engine routes
`needs_user` decisions to a surface for approval and records the outcome.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

# Shell metacharacters that turn one "allowlisted" command into several. Any of these in a
# command disqualifies it from allowlist auto-run — approval is required instead. Covers
# chaining (`;` `&` `&&` `||`), pipes (`|`), redirection (`>` `<`), command substitution
# (`` ` `` `$(`), process substitution / grouping (`(`), and newlines.
_SHELL_OPERATORS = (";", "&", "|", ">", "<", "`", "$(", "(", "\n", "\r")


def _has_shell_operators(command: str) -> bool:
    return any(op in command for op in _SHELL_OPERATORS)

from .approval_store import ApprovalStore
from .risk import (  # re-exported for back-compat (manager.py imports WRITE_TOOLS)
    SHELL_TOOL,
    WRITE_TOOLS,
    RiskClass,
    RiskOverrides,
    classify,
    is_consequential,
)


class Mode(str, Enum):
    DISCUSS = "discuss"  # read-only conversation: no edits, no planning workflow
    PLAN = (
        "plan"  # read-only + the planning contract (explore → propose_plan → execute)
    )
    INTERACTIVE = "interactive"  # ask for approval (default)
    AUTO = "auto"  # full access
    CUSTOM = "custom"  # interactive + auto-allow the config's `auto_allow` tools


# Modes whose enforcement is read-only. DISCUSS and PLAN share the same gate; they differ
# only in intent — PLAN additionally drives the agent toward a propose_plan approval.
READ_ONLY_MODES = frozenset({Mode.DISCUSS, Mode.PLAN})


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    needs_user: bool = False  # True → surface should prompt the user for approval
    # Set when a task-scoped standing rule allowed the call ("tool → target") so the
    # engine can audit the exact rule and the tool card can say so (§25).
    rule: str = ""


def standing_rule_candidate(
    tool_name: str,
    arguments: dict[str, Any],
    metadata: Any = None,
    overrides: Optional[RiskOverrides] = None,
) -> Optional[str]:
    """The target value iff this call is eligible for a task-scoped standing rule
    (UX-DECISIONS §25): external-risk only (never exec/write-local — shell asks forever),
    the tool must declare a target argument, and the call must actually name a target.
    Returns None otherwise — ineligible calls keep parking approvals as today."""
    from .connectors.tool_defs import target_arg_for

    if classify(tool_name, metadata, overrides) is not RiskClass.EXTERNAL:
        return None
    arg = target_arg_for(tool_name)
    if arg is None:
        return None
    value = str((arguments or {}).get(arg) or "").strip()
    return value or None


@dataclass
class PermissionEngine:
    workspace_root: Path
    mode: Mode = Mode.INTERACTIVE
    allowed_commands: list[str] = field(default_factory=list)
    auto_allow_tools: set[str] = field(default_factory=set)
    session_allow_tools: set[str] = field(default_factory=set)
    session_allow_commands: set[str] = field(default_factory=set)
    # Task-scoped standing rules (§25): {tool: {allowed targets}}, seeded from the owning
    # ScheduledTask's target-shaped entries. Kept by reference and re-read every check, so a
    # rule minted mid-run ("Allow every time") applies to the run's next call too.
    task_rules: dict[str, set[str]] = field(default_factory=dict)
    # Persistent pre-approvals (~/.config/coworker/approvals.json): grants that outlive
    # the session. Consulted like the session allowlists; None → feature off.
    persistent: Optional["ApprovalStore"] = None
    # User-local risk override resolver (Phase 2). None → use the base classification.
    risk_overrides: Optional[RiskOverrides] = None
    # Shared, possibly-mutable list of roots (RootDir-like / dicts). When omitted, the single
    # `workspace_root` is the sole writable root (back-compat). Kept by reference and re-read on
    # every check, so runtime add/remove of folders takes effect without rebuilding the engine.
    roots: Optional[list] = None

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        self.auto_allow_tools = set(self.auto_allow_tools)
        if self.roots is None:
            self.roots = [{"path": self.workspace_root, "writable": True}]

    def _resolved_roots(self) -> list[tuple[Path, bool]]:
        out: list[tuple[Path, bool]] = []
        for r in self.roots or []:
            if isinstance(r, dict):
                p, w = r["path"], bool(r.get("writable", False))
            elif isinstance(r, (str, Path)):
                p, w = r, True
            else:  # duck-typed RootDir-like
                p, w = getattr(r, "path"), bool(getattr(r, "writable", False))
            out.append((Path(p).expanduser().resolve(), w))
        return out

    def evaluate(
        self, tool_name: str, arguments: dict[str, Any], metadata: Any = None
    ) -> Decision:
        arguments = arguments or {}
        is_connector = getattr(metadata, "category", "") == "connector"
        risk = classify(tool_name, metadata, self.risk_overrides)
        is_write = risk is RiskClass.WRITE_LOCAL
        is_shell = risk is RiskClass.EXEC
        consequential = is_consequential(risk)

        # Discuss / plan modes: read-only.
        if self.mode in READ_ONLY_MODES and consequential:
            return Decision(
                False, f"{self.mode.value} mode is read-only", needs_user=False
            )

        # Path scoping for writes that name a path (all modes): must land in a writable root.
        if is_write:
            path = arguments.get("path")
            if path is not None and not self._under_writable_root(path):
                return Decision(False, f"path is not in a writable directory: {path}")

        # Non-consequential tools always run.
        if not consequential:
            return Decision(True, "low risk")

        # Full access.
        if self.mode is Mode.AUTO:
            return Decision(True, "full access")

        # interactive / custom: allowlists.
        command = str(arguments.get("command", "")) if is_shell else ""
        if is_shell:
            if self._command_allowed(command):
                return Decision(True, "command on allowlist")
            if command and command in self.session_allow_commands:
                return Decision(True, "command allowed for session")
        if tool_name in self.session_allow_tools and not is_connector:
            return Decision(True, "tool allowed for session")

        # Persistent pre-approvals: behave as an always-on session grant. Blanket tool
        # grants respect the same connector exclusion as the session list, plus the
        # scope rule — external tools with a declared target argument are only ever
        # allowed per-target (the target check lives after task_rules below).
        if self.persistent is not None:
            if is_shell and command and command in self.persistent.allow_commands():
                return Decision(True, "command allowed permanently")
            if (
                not is_connector
                and tool_name in self.persistent.allow_tools()
                and not self._blanket_ineligible(tool_name, metadata)
            ):
                return Decision(True, "tool allowed permanently")

        # Task-scoped standing rules (§25): tool + exact target, owned by the automation.
        # Deliberately NOT subject to the connector exclusion above — the exact-target
        # binding is what makes auto-allowing a connector tool safe. Never for exec risk
        # (candidate extraction is external-risk-only), and additive on top of the mode:
        # read-only modes already returned before this point.
        if tool_name in self.task_rules:
            target = standing_rule_candidate(
                tool_name, arguments, metadata, self.risk_overrides
            )
            if target and target in self.task_rules[tool_name]:
                rule = f"{tool_name} → {target}"
                return Decision(True, f"allowed by standing rule: {rule}", rule=rule)

        # Persistent target pins: like a standing rule, but owned by the user forever.
        # Connector tools included — the exact-target binding is the safety.
        if self.persistent is not None:
            targets = self.persistent.allow_targets().get(tool_name) or set()
            if targets:
                target = standing_rule_candidate(
                    tool_name, arguments, metadata, self.risk_overrides
                )
                if target and target in targets:
                    rule = f"{tool_name} → {target}"
                    return Decision(
                        True, f"allowed by permanent rule: {rule}", rule=rule
                    )

        # Custom mode auto-approves the configured tools.
        if self.mode is Mode.CUSTOM and tool_name in self.auto_allow_tools:
            return Decision(True, "auto-allowed by config")

        # Otherwise: ask the user.
        return Decision(False, "requires approval", needs_user=True)

    # -- session memory ---------------------------------------------------------
    def allow_tool_for_session(self, tool_name: str) -> None:
        self.session_allow_tools.add(tool_name)

    def allow_command_for_session(self, command: str) -> None:
        if command:
            self.session_allow_commands.add(command)

    # -- persistent memory ------------------------------------------------------
    def _blanket_ineligible(self, tool_name: str, metadata: Any) -> bool:
        """External tools with a declared target argument can only be allowed
        per-target — a blanket grant would let one approval authorize sends to
        anyone (the spec's scope rule)."""
        from .connectors.tool_defs import target_arg_for

        risk = classify(tool_name, metadata, self.risk_overrides)
        return risk is RiskClass.EXTERNAL and target_arg_for(tool_name) is not None

    def grant_persistent(
        self, tool_name: str, arguments: dict[str, Any], metadata: Any = None
    ) -> str:
        """Route a permanent grant to its scope — exact command for shell, exact
        target for external send tools, tool name otherwise. Returns an audit label,
        '' when the grant is refused (no store, empty command, or no target where
        one is required)."""
        if self.persistent is None:
            return ""
        risk = classify(tool_name, metadata, self.risk_overrides)
        if risk is RiskClass.EXEC:
            command = str((arguments or {}).get("command", ""))
            if not command:
                return ""
            self.persistent.grant_command(command)
            return f"command: {command}"
        # Connector tools never get blanket grants (mirroring evaluate's session-list
        # exclusion — a blanket entry would be dead weight there anyway), so for them —
        # and for any external tool with a declared target argument — the only valid
        # permanent grant is a target pin. No pinnable target → refuse.
        is_connector = getattr(metadata, "category", "") == "connector"
        if is_connector or self._blanket_ineligible(tool_name, metadata):
            target = standing_rule_candidate(
                tool_name, arguments, metadata, self.risk_overrides
            )
            if not target:
                return ""
            self.persistent.grant_target(tool_name, target)
            return f"{tool_name} → {target}"
        self.persistent.grant_tool(tool_name)
        return f"tool: {tool_name}"

    # -- helpers ----------------------------------------------------------------
    def _candidate(self, path: str) -> Path:
        # Relative paths resolve against the primary (workspace_root); absolute/`~` taken as-is.
        p = Path(path).expanduser()
        return p.resolve() if p.is_absolute() else (self.workspace_root / p).resolve()

    def _under_root(self, path: str) -> bool:
        candidate = self._candidate(path)
        for rp, _ in self._resolved_roots():
            try:
                candidate.relative_to(rp)
                return True
            except ValueError:
                continue
        return False

    def _under_writable_root(self, path: str) -> bool:
        candidate = self._candidate(path)
        for rp, writable in self._resolved_roots():
            if not writable:
                continue
            try:
                candidate.relative_to(rp)
                return True
            except ValueError:
                continue
        return False

    def _command_allowed(self, command: str) -> bool:
        # An allowlist entry auto-runs a command WITHOUT approval, so prefix matching is
        # unsafe: `git status` would auto-approve `git status && rm -rf ~`. Reject anything
        # carrying shell operators (chaining/redirection/substitution) up front, then match
        # the parsed argv against each entry — the entry's own tokens must be an exact
        # prefix of the command's tokens (so `git status` matches `git status -s` but never
        # `git statusfoo` or a bare `git`).
        if _has_shell_operators(command):
            return False
        try:
            argv = shlex.split(command)
        except ValueError:
            return False  # unbalanced quotes etc. — treat as not-allowlisted
        if not argv:
            return False
        for allowed in self.allowed_commands:
            try:
                prefix = shlex.split(allowed)
            except ValueError:
                continue
            if prefix and argv[: len(prefix)] == prefix:
                return True
        return False
