# Persistent Tool Pre-approvals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "Always allow (permanent)" on every approval surface, backed by `~/.config/coworker/approvals.json`, so routine tools stop re-prompting every session.

**Architecture:** A small persistent store (mtime-cached, atomic writes) consulted by `PermissionEngine.evaluate()` alongside the existing session allowlists. A new `ApprovalOutcome.ALWAYS_PERSISTENT` flows through the exact resolution path `always_tool` uses today; the engine routes the grant to the right scope (command / target / tool) so the security rule lives in one place. Server exposes GET/DELETE for the Settings screen; `buttons_for` adds an "Always" button for messaging.

**Tech Stack:** Python stdlib; FastAPI routes in `server/app.py`; React + i18n catalogs in `surfaces/gui`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-31-persistent-approvals-design.md`.
- Scope rule: EXTERNAL-risk tools that declare a target arg NEVER get blanket `allow_tools` — target-pinned grants only. Enforced in `grant_persistent` AND ignored on read.
- Store path: `state_dir() / "approvals.json"` (same dir as `risk_overrides.json`). Never in git.
- Commits: imperative English, no prefix, no Claude coauthor.
- GUI copy through i18n catalogs (en + pt-BR); `noHardcodedCopy` guard allowlist is exact.

---

### Task 1: `coworker/approval_store.py` + tests

**Files:** Create `coworker/approval_store.py`, `tests/test_approval_store.py`.

**Produces:** `ApprovalStore(path)` with `allow_tools() -> set[str]`, `allow_commands() -> set[str]`, `allow_targets() -> dict[str, set[str]]`, `grant_tool/grant_command/grant_target`, `revoke_tool/revoke_command/revoke_target`, `snapshot() -> dict`.

- [ ] Failing tests: round-trip each grant/revoke; corrupt/missing file → empty; mtime reload (write file externally → next read sees it); atomic write leaves valid JSON.
- [ ] Implement: JSON `{"allow_tools": [], "allow_commands": [], "allow_targets": {}}`; `_load()` with mtime cache; `_save()` tmp+rename; tolerant parse (non-dict → empty).
- [ ] `pytest tests/test_approval_store.py` green → commit "Add persistent approval store".

### Task 2: PermissionEngine integration + `grant_persistent`

**Files:** Modify `coworker/permissions.py`; test in `tests/test_permissions*.py` (follow existing test module naming).

- [ ] Failing tests: engine with store honors permanent tool / command / target; connector tool name in `allow_tools` ignored; EXTERNAL+target-arg tool in `allow_tools` ignored; `grant_persistent` routes EXEC→command, EXTERNAL+target→target, EXTERNAL+target-arg-but-empty→no grant, plain tool→tool.
- [ ] Implement: field `persistent: Optional[ApprovalStore] = None`. In `evaluate()`, directly after the session-allowlist checks:

```python
        if self.persistent is not None:
            if is_shell and command and command in self.persistent.allow_commands():
                return Decision(True, "command allowed permanently")
            if (
                not is_connector
                and tool_name in self.persistent.allow_tools()
                and not self._blanket_ineligible(tool_name, metadata)
            ):
                return Decision(True, "tool allowed permanently")
```

  (hoist `command = str(arguments.get("command", ""))` so both shell checks share it). After the task-rules block, the permanent target rule:

```python
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
```

  Helpers:

```python
    def _blanket_ineligible(self, tool_name: str, metadata: Any) -> bool:
        """EXTERNAL tools with a declared target argument can only be allowed
        per-target — a blanket grant would let one approval authorize sends to
        anyone (spec scope rule)."""
        from .connectors.tool_defs import target_arg_for

        risk = classify(tool_name, metadata, self.risk_overrides)
        return risk is RiskClass.EXTERNAL and target_arg_for(tool_name) is not None

    def grant_persistent(
        self, tool_name: str, arguments: dict[str, Any], metadata: Any = None
    ) -> str:
        """Route a permanent grant to its scope; returns an audit label ('' = refused)."""
        if self.persistent is None:
            return ""
        risk = classify(tool_name, metadata, self.risk_overrides)
        if risk is RiskClass.EXEC:
            command = str((arguments or {}).get("command", ""))
            if not command:
                return ""
            self.persistent.grant_command(command)
            return f"command: {command}"
        if self._blanket_ineligible(tool_name, metadata):
            target = standing_rule_candidate(
                tool_name, arguments, metadata, self.risk_overrides
            )
            if not target:
                return ""
            self.persistent.grant_target(tool_name, target)
            return f"{tool_name} → {target}"
        self.persistent.grant_tool(tool_name)
        return f"tool: {tool_name}"
```

- [ ] Suite green → commit "Honor persistent pre-approvals in the permission engine".

### Task 3: outcome + wiring

**Files:** Modify `coworker/engine.py` (enum + resolution), `coworker/agent.py:278` (construct store), test alongside existing engine/approval tests.

- [ ] `ApprovalOutcome.ALWAYS_PERSISTENT = "always_persistent"`; in the outcome handling (`engine.py:595`):

```python
                elif outcome is ApprovalOutcome.ALWAYS_PERSISTENT:
                    granted = self.permissions.grant_persistent(
                        tool_call.name, tool_call.arguments, metadata
                    )
                    if granted:
                        self._audit(
                            tool_call,
                            stage="persistent_rule_minted",
                            status="granted",
                            reason=f"always allow (permanent): {granted}",
                        )
```

- [ ] `agent.py`: `persistent=ApprovalStore(state_dir() / "approvals.json")` in the `PermissionEngine(...)` call (import at top). `manager.approval_outcome` needs NO change — `ApprovalOutcome(resolution)` already resolves the new value.
- [ ] Failing test first: resolution "always_persistent" → tool runs AND store gains the grant; second session (fresh engine, same store) does not prompt.
- [ ] Suite green → commit "Persist always-allow approvals across sessions".

### Task 4: messaging button

**Files:** Modify `coworker/interactions.py` (`buttons_for`), its tests.

- [ ] Approval items gain `Button("Always", encode(item.id, "always_persistent"))` between Approve and Deny. Test asserts the three buttons and the encoded resolution.
- [ ] Commit "Offer a permanent allow button on messaging approvals".

### Task 5: server API

**Files:** Modify `coworker/server/app.py` (near the connectors routes), `tests/test_server_approvals.py` (new, TestClient pattern from `test_connectors_allowlist.py`).

- [ ] `GET /v1/approvals` → `store.snapshot()`; `POST /v1/approvals/revoke` body `{"kind": "tool"|"command"|"target", "value": str, "tool": str|None}` → revoke + return snapshot. Store instance from the manager (same path as agent.py).
- [ ] Tests: snapshot reflects grants; revoke each kind. Commit "Expose persistent approvals over the server API".

### Task 6: GUI

**Files:** Modify `surfaces/gui/src/components/ApprovalCard.tsx`, `SettingsView.tsx`, `api.ts`, i18n catalogs `en.ts` / `pt-BR.ts` (+ guard allowlist only if a literal is intentional), tests beside each.

- [ ] `ApprovalDecision` type gains `"always_persistent"`.
- [ ] ApprovalCard: permanent button beside the session one (non-connector, non-run_shell: "Sempre permitir (permanente)"; run_shell: permanent-command variant; connector with `standingTarget`: target-pinned label). Existing `always_tool` label re-worded to say session-scoped.
- [ ] SettingsView: "Aprovações" section — three lists (tools, commands, targets) from `GET /v1/approvals`, remove button per row → revoke endpoint.
- [ ] All copy via `t(...)` in BOTH catalogs. Run `cd surfaces/gui && npm test` — includes the i18n guard.
- [ ] Commit "Add permanent approvals to the approval card and settings".

### Task 7: ship

- [ ] Full `pytest` + `npm test`. Push, PR to deploy/hml, CodeRabbit follow-up, squash-merge, rebuild (`build_dmg.sh` + install + codesign + relaunch).

## Self-review

Spec coverage: store→T1, policy+scope rule→T2, outcome/wiring→T3, messaging→T4, API→T5, Settings/Card→T6, non-goals untouched. Types consistent (`ApprovalStore` API used identically T1–T3/T5). No placeholders in core tasks; T5/T6 reference existing patterns by file.
