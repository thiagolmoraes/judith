# Claude Code bridge, phase 2 — hooks: exact status + proactive notify

**Date:** 2026-07-30
**Status:** approved
**Builds on:** `2026-07-30-whatsapp-claude-bridge-design.md` (phase 1, merged as #32)

## Problem

Phase 1 answers "did the session finish?" by heuristics: transcript mtime and tail
guesses (`transcript_confidence: "guessed"`), and only when the owner asks (pull).
Phase 2 replaces guessing with facts pushed by Claude Code itself, and adds a proactive
"session finished" WhatsApp notification for sessions the owner explicitly watches.

Scenario simulation drove the scope (predicted hit rates in parentheses):

- Pull "did it finish?" goes from ~70–85% (heuristic) to ~100% (exact registry).
- Two sessions in the same project: ~70% → ~100% (exact `session_id`/`transcript_path`).
- Proactive notify: ~90% with the app open; best-effort by design when closed — the
  registry persists, so a later pull is always consistent.
- Remote approval of permission prompts via WhatsApp (~40–60% hit without dedicated
  keystroke work): **out of scope — phase 3.** Phase 2 only *reports*
  "waiting for approval"; the decision happens at the terminal.
- Notify-every-turn would spam (every chat reply at the terminal ends a turn):
  notifications are **opt-in per session** ("watch this session"), one-shot.

## Decisions (locked during brainstorm)

1. **Transport: watched files only.** The hook writes to `~/.claude/ow-bridge/`;
   OpenWorker polls that directory. No POST to the sidecar — the sidecar's dynamic
   port + token would make the hook fragile, and a file registry keeps events durable
   when the app is closed.
2. **Registry is the source of truth; discovery is the liveness test.** A registry
   entry whose recorded pid is no longer alive is a ghost (crashed session,
   `SessionEnd` never fired): pruned and ignored.
3. **Watch is one-shot.** "Tell me when it finishes" → one notification → watch
   consumed. Watching again is asking again.
4. **The notification sends a template, not a model turn.** Project, branch, and a
   snippet of the session's last assistant message — composed in code, sent directly
   through the existing connector send path. No LLM cost to notify.

## Architecture

```
Claude Code ──Stop/Notification hooks──►  ~/.claude/ow-bridge/sessions/<id>.json
                                                   ▲                │
        `coworker claude-bridge install-hooks` ────┘                │  poll (~2 s)
                                                                    ▼
WhatsApp ◄── Evolution adapter (existing) ◄── watcher ◄── registry + phase-1 discovery
                                                              (liveness cross-check)
```

New modules live in the phase-1 domain package; dependencies still point inward:

```
coworker/claude_bridge/
  hook_script.py   # standalone — runs inside Claude Code's hook, stdlib only,
                   # zero imports from coworker
  registry.py      # read/write ~/.claude/ow-bridge (sessions + watches)
  watcher.py       # poll loop: detect transitions, prune ghosts, notify
  discovery.py     # (upgrade) consult the registry before the mtime heuristic
coworker/tools/claude_sessions.py   # (upgrade) + watch/unwatch tools
coworker/cli.py                     # (upgrade) `claude-bridge install-hooks`
```

SOLID: `hook_script` is standalone by necessity (it runs in Claude Code's process
context, not OpenWorker's). `watcher` depends on `registry`, `SessionDiscovery`, and a
`Notifier` protocol — the WhatsApp implementation is injected, tests pass a fake.

## Components

### `hook_script.py` (standalone, stdlib only)

Invoked by Claude Code hooks with a JSON payload on stdin (`session_id`,
`transcript_path`, `cwd`, `hook_event_name`; `Notification` adds `message`). Writes
`~/.claude/ow-bridge/sessions/<session_id>.json` atomically (tmp file + `os.replace`):

```json
{
  "session_id": "…",
  "transcript_path": "/Users/x/.claude/projects/…/….jsonl",
  "cwd": "/Users/x/dev/webhook",
  "pid": 91015,
  "status": "idle",
  "event": "Stop",
  "message": null,
  "updated_at": "2026-07-30T15:00:00+00:00"
}
```

- `pid` is `os.getppid()` — the hook runs as a child of the `claude` process, so the
  parent pid is the session's pid. This is what the watcher's liveness check uses.
- Event → status: `Stop` → `idle`; `Notification` → `waiting_approval` (with the
  payload's `message`); `SessionEnd` → `ended`. `SubagentStop` and anything
  unrecognised: exit 0, write nothing (no false "finished" from a subagent).
- Any failure (unreadable stdin, unwritable dir) exits 0 silently — a hook must never
  break or slow the session it observes.

### `registry.py`

- `read_sessions(dir) -> list[SessionState]` — parse every `sessions/*.json`;
  malformed or half-written files are skipped, never raised on (same tolerance
  contract as the phase-1 transcript parser).
- `prune(dir, alive_pids) -> list[SessionState]` — delete entries whose pid is not in
  `alive_pids` or whose status is `ended`; returns what was removed (the watcher
  notifies if a watched session died unfinished).
- `Watches` — `watches.json` in the same dir: `{session_id: {chat_id, platform,
  created_at}}`. `add`, `remove`, `pop(session_id)` (one-shot consumption). Atomic
  writes, tolerant reads.

### `watcher.py`

`BridgeWatcher(registry_dir, discovery, notifier, poll_seconds=2)` — an asyncio task
started with the local agent server (same lifecycle as the scheduler tick):

1. Read registry; get alive pids from `SessionDiscovery` (one `ps` per poll).
2. Prune ghosts. A pruned session that was watched → notify
   "⚠️ session <project> closed before finishing" and consume the watch.
3. For each watched session whose status transitioned to `idle` since the last poll →
   build the message — project (cwd basename), branch, last assistant snippet from the
   transcript (phase-1 `tail`) — send via `Notifier`, consume the watch.
4. `waiting_approval` transition on a watched session → notify
   "⏸ session <project> is waiting for approval: <message>" (watch NOT consumed — the
   session hasn't finished).

```python
class Notifier(Protocol):
    def send(self, platform: str, chat_id: str, text: str) -> bool: ...
```

Production `Notifier` wraps the existing connector send path (the same machinery
`send_message` uses); a failed send logs and drops — the registry keeps the state, so
a later pull still answers correctly.

### Tool upgrades (`tools/claude_sessions.py`)

- `watch_claude_session(tty)` — resolve tty → session via discovery (which now knows
  exact ids from the registry), record the watch with the **originating chat** of the
  current conversation (platform + chat_id supplied by the session's context, the same
  way `send_message` learns its reply target). Errors: `session_gone`,
  `already_watched`, `no_registry` (hooks not installed — the error text tells the
  agent to suggest `coworker claude-bridge install-hooks`).
- `unwatch_claude_session(tty)` — remove; idempotent.
- `find_claude_sessions` gains `status` (from the registry when present: `idle` /
  `waiting_approval` / `running`*) and `watched: bool`.
  *`running` is inferred: registry entry exists and the last event is older than the
  transcript's latest activity — the session started a new turn since the last Stop.

### Discovery upgrade

Before the mtime heuristic: match registry entries by pid. Hit → exact
`transcript_path`, `transcript_confidence: "matched"`. Miss (hooks not installed, or
session started before install) → phase-1 heuristic unchanged. Phase 2 degrades to
phase 1, never below it.

### Installer (`coworker claude-bridge install-hooks`)

Registers the three hooks (`Stop`, `Notification`, `SessionEnd`) in
`~/.claude/settings.json`, invoking `hook_script.py` via the current Python. Merge,
never clobber: existing user hooks are preserved; running twice is a no-op
(idempotent); `--uninstall` removes exactly what install added. Backs up the settings
file before the first write.

## Error handling (mapped to the simulation)

| # | Failure | Behaviour |
|---|---|---|
| C1 | — (no POST anymore) | eliminated by the file-only transport |
| C2 | OpenWorker closed when the turn ends | no notification (by design); registry persists; next pull is exact |
| C3 | Notification payload without command detail | generic "waiting for approval" text; message field included when present |
| C6 | session crashes, `SessionEnd` never fires | pid liveness prune; watched → "closed before finishing" notify |
| C7 | chatty session would spam | only watched sessions notify, one-shot |
| C8 | subagent stops | `SubagentStop` ignored by the hook script |
| C9 | hook slow/broken | exits 0 always; Claude Code's hook timeout contains it; watcher tolerates malformed files |
| C10 | WhatsApp/Evolution down at notify time | send fails → logged and dropped; registry state still answers the next pull |
| — | hooks not installed | everything degrades to phase-1 behaviour; watch tool returns `no_registry` with install instructions |
| — | two OpenWorker instances | poll + one-shot `pop` keeps a watch from double-firing (last reader wins the pop) |

## Testing

Same regime as phase 1 — no test touches real `ps`, hooks, `~/.claude`, or the
network; everything injected, green on Linux CI.

- `tests/test_claude_bridge_hook_script.py` — stdin fixtures per event type: writes
  the right state file atomically, ignores `SubagentStop`, exits 0 on garbage stdin
  and on an unwritable directory.
- `tests/test_claude_bridge_registry.py` — tmp_path registries: tolerant reads,
  prune by pid/`ended`, watch add/pop one-shot semantics, atomic write leaves no
  partial file behind.
- `tests/test_claude_bridge_watcher.py` — fake registry dir + fake discovery + fake
  notifier + injected clock: idle transition notifies once and consumes the watch;
  `waiting_approval` notifies without consuming; ghost prune notifies "closed before
  finishing"; unwatched sessions never notify; notifier failure doesn't crash the loop.
- `tests/test_claude_session_tools.py` (extended) — watch/unwatch contracts
  (`session_gone`, `already_watched`, `no_registry`, idempotent unwatch), `status` and
  `watched` fields in `find_claude_sessions`.
- `tests/test_claude_bridge_installer.py` — tmp settings.json: fresh install, merge
  with existing user hooks, idempotence, uninstall removes only ours, backup created.

## Out of scope

- Remote approval of permission prompts (phase 3: dedicated keystroke handling).
- POST/webhook transport to the sidecar.
- Watching from platforms other than the chat the request came from.
