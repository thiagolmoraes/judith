# Claude Code bridge, phase 3 — remote approval of permission prompts

**Date:** 2026-07-30
**Status:** approved
**Builds on:** phase 1 (#32, terminal driver + tools) and phase 2 (#33, hook registry with `waiting_approval` + message).

## Problem

Phase 2 tells the owner on WhatsApp that a session is waiting for approval and for
what. Acting on it still requires the terminal. Phase 3 closes the loop: "aprova" /
"nega" from WhatsApp presses the right key in the right iTerm2 tab — with an
echo-confirm step so a hasty reply can never approve the wrong thing.

The phase-2 simulation put naive remote approval at ~40–60% (permission prompts are
interactive menus, not free text). Two facts fix that:

- Claude Code permission prompts accept a **bare number key**: `1` approves, `3`
  denies — no Enter. iTerm2 AppleScript supports `write text "1" newline NO`,
  a keystroke without the trailing Enter that phase 1's `send_text` always adds.
- The hook registry knows the session is `waiting_approval` and carries the prompt
  message — enough to verify state before pressing and to detect the effect after.

Revised predictions: standard prompt approved ~90–95%; a prompt that changed between
echo and confirmation is 100% protected (see token design); an atypical menu (e.g. a
plan-approval prompt without option 3) fails **reported**, never silently.

## Decisions (locked during brainstorm)

1. **Scope: approve + deny only.** `approve` → key `1`, `deny` → key `3`. "Deny with
   typed instruction" is out (two-step, fragile input layout).
2. **Echo-confirm, enforced by the tool contract.** The first call returns the exact
   prompt text and a confirmation token; the keystroke only happens on a second call
   with that token, after the owner explicitly confirmed on WhatsApp.
3. **Stateless token.** `sha256(session_id + prompt_message)[:12]`. The second call
   recomputes it from the prompt *currently* waiting: if another request replaced the
   one that was echoed, the hash differs → `prompt_changed` + fresh echo. Confirmation
   is bound to the exact prompt text; no storage, no TTL bookkeeping.
4. **Post-keystroke verification.** After pressing, the session's transcript mtime
   must advance within ~10 s (approval and denial both make the turn continue). It
   does → `verified: true`; it doesn't → `status: "sent_unverified"` so the owner
   knows the key may not have landed.
5. **Residual risk accepted:** the model could skip the echo and pass the token it
   just received. The tool description forbids it explicitly ("only pass
   confirm_token after the user explicitly confirmed, after you showed them the
   prompt"); full enforcement is impossible at the tool layer and the owner's
   allow-listed number remains the outer gate.

## Design

No new modules. Two extensions:

### `claude_bridge/terminal.py` — `send_keys`

```python
class TerminalDriver(Protocol):
    def find_target(self, tty: str) -> str | None: ...
    def send_text(self, target: str, text: str) -> bool: ...
    def send_keys(self, target: str, keys: str) -> bool: ...   # NEW — no trailing Enter
```

`ITerm2Driver.send_keys` uses the same session-by-id AppleScript as `send_text` with
`write text "{keys}" newline NO`. Same contract: `False` on failure, never raises.

### `tools/claude_sessions.py` — `respond_to_claude_prompt`

`respond_to_claude_prompt(tty, decision, confirm_token=None)`, registered with the
other bridge tools (requires `watches`-style wiring? no — it requires the registry via
discovery's `status`/`session_id`, present whenever phase 2 is; registered always,
alongside the other five).

Flow:

1. Validate: `tty` non-blank; `decision` in `{"approve", "deny"}`; token, when given,
   a string.
2. Resolve session by tty → `session_gone` if absent.
3. `session.status != "waiting_approval"` → `{"error": "not_waiting", "current_status": <atual>}`
   (also covers `session_id is None` → `no_registry` + install hint, same as watch).
4. Read the waiting prompt message from the registry (`SessionState.message`, may be
   empty → treated as `""` for hashing; the echo then says "a permission request").
5. `expected = sha256(session_id + "\n" + message).hexdigest()[:12]`.
   - No token → `{"status": "confirmation_required", "prompt": message,
     "confirm_token": expected}`.
   - Token ≠ expected → `{"error": "prompt_changed", "prompt": message,
     "confirm_token": expected}` (the echoed request is gone; re-echo).
   - Token == expected → proceed.
6. `driver.find_target` → `session_gone` if the tab closed; snapshot the transcript
   mtime; `driver.send_keys(target, "1" | "3")` → `send_failed` on False.
7. Verify: poll (1 s, injectable sleep/clock) up to 10 s for transcript mtime >
   snapshot. Advanced → `{"status": "approved"|"denied", "verified": true,
   "prompt": message}`. Not advanced (or no transcript) →
   `{"status": "sent_unverified", "decision": ..., "prompt": message}`.

The tool description carries the protocol: first call without token; relay the prompt
to the user verbatim; call again with the token **only after** the user explicitly
confirms; never fabricate or replay tokens.

## Error handling

| Failure | Behaviour |
|---|---|
| Session not waiting (finished meanwhile, or never was) | `not_waiting` + current status — the agent tells the owner what actually happened |
| Hooks not installed | `no_registry` + install hint (same contract as watch) |
| Prompt swapped between echo and confirm | hash mismatch → `prompt_changed` + new echo; wrong-command approval impossible |
| Tab closed between calls | `session_gone` |
| Keystroke lands on an atypical menu (no option 3, plan prompt) | transcript doesn't move → `sent_unverified`, reported to the owner |
| Registry message empty | echo says "a permission request"; hash over `""` still binds to the session |
| Model tries to skip the echo | forbidden by the tool contract (residual risk accepted — decision 5) |

## Testing

Same hermetic regime. Extensions to existing files:

- `tests/test_claude_bridge_terminal.py` — `send_keys`: generates `newline NO`, no
  trailing Enter, escaping, `False` on failure/crash; fake driver in tools tests gains
  `send_keys` recording.
- `tests/test_claude_session_tools.py` — the full flow: confirmation_required with
  token on first call; approve with valid token presses "1"; deny presses "3";
  stale token → `prompt_changed` with fresh token; `not_waiting`; `no_registry`;
  `session_gone` (unknown tty and dead target); `send_failed`; verified success
  (mtime advances via fake clock); `sent_unverified` when mtime frozen; invalid
  decision / blank tty → `invalid_arguments`; empty registry message hashes and
  echoes the fallback text.

## Out of scope

- Deny with typed instruction (two-step input).
- Any prompt-type detection beyond the verify-by-transcript effect.
- Non-iTerm2 drivers (the `send_keys` seam extends the existing protocol).
