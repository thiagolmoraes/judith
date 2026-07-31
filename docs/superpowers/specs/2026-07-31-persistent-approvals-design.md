# Persistent tool pre-approvals — design

Date: 2026-07-31
Status: approved (decisions taken with the owner in session)

## Problem

The approval system re-asks for the same tool over and over. "Always allow" exists on
the ApprovalCard (`always_tool`) but is session-scoped (`session_allow_tools`,
`coworker/permissions.py:88`): it dies with the session, and every new session —
including every scheduled-task run — prompts again. Messaging surfaces
(WhatsApp/Telegram buttons via `mirror_inbox_item`) offer only Approve/Deny, so a
phone-first owner cannot even grant the session-scoped variant. Observed pain:
`delete_scheduled_task` prompting "toda hora" for routine reminder cleanup.

There is no persistent pre-approval store anywhere (verified: grants are minted at
task creation or held in per-session sets only).

## Decision

A persistent pre-approval store consulted by the permission policy, fed by a new
"always (permanent)" resolution available on every approval surface, managed from a
Settings screen.

### Store

`~/.config/coworker/approvals.json` (same directory as `prefs.json`; never in git):

```json
{
  "allow_tools": ["delete_scheduled_task"],
  "allow_commands": ["git status"],
  "allow_targets": {"send_message": ["whatsapp_evolution:5511999999999"]}
}
```

- `allow_tools` — tool names allowed without approval, forever.
- `allow_commands` — exact shell commands (same matching as `session_allow_commands`).
- `allow_targets` — per-tool target allowlists for external send tools.

Atomic writes (tmp + rename). Tolerant reads (missing/corrupt file → empty store).
Mtime-checked cache so edits apply to live sessions without restart.

### Scope rule (the one security decision)

Tools whose risk is EXTERNAL **and** that take an arbitrary target argument
(`standing_rule_candidate` eligibility: `send_message`, `send_file`, connector send
tools) can NOT enter `allow_tools` — only `allow_targets`, pinned to an exact target.
Everything else (automation management, local writes, shell commands) is eligible for
blanket `allow_tools`/`allow_commands`. Enforced in the policy (a non-eligible tool in
`allow_tools` is ignored) and reflected in the UI (the permanent option for send tools
reads "always allow to this target").

### Policy integration

`PermissionPolicy.evaluate()` consults the persistent store exactly where it consults
`session_allow_tools` / `session_allow_commands` / `task_rules` today — persistent
grants behave as an always-on session grant. New outcome `always_persistent` flows
through the same PERMISSION_REQUIRED resolution path as `always_tool` does now
(`engine.py:595`, `manager.py:2679`), plus a store write.

### Surfaces

1. **ApprovalCard (GUI)**: new "Sempre permitir (permanente)" choice; the existing
   `always_tool` label becomes explicitly session-scoped ("só nesta sessão"). For
   send tools the permanent option is target-pinned.
2. **Messaging buttons** (`interactions.buttons_for`): gains the permanent choice with
   the same eligibility rules — resolves from WhatsApp/Telegram without opening the app.
3. **Settings ▸ Aprovações**: lists current permanent grants (tools, commands,
   targets), one-click remove. All copy through i18n catalogs (en + pt-BR) with the
   hardcoded-copy guard satisfied.

## Non-goals

- No per-persona scoping in v1 (store is global to the install).
- No expiry/TTL on grants (remove is manual via Settings).
- No import of existing session grants.

## Testing

- Python: store round-trip + corrupt-file tolerance; `evaluate()` honors each of the
  three lists; eligibility rule (send tool in `allow_tools` ignored, honored in
  `allow_targets`); `always_persistent` resolution persists and applies immediately;
  live-reload via mtime.
- GUI: ApprovalCard renders the new choice and posts the right resolution; Settings
  list renders and removes entries; i18n keys present in both catalogs.
