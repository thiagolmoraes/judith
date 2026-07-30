# WhatsApp ⇄ Claude Code session bridge

**Date:** 2026-07-30
**Status:** approved

## Problem

The owner runs long-lived Claude Code sessions in iTerm2 tabs on the same machine that
runs OpenWorker. From WhatsApp they want to ask things like "did the webhook session
finish?", have OpenWorker locate the right live session, optionally send it input, and
get the answer back — all mediated by the existing WhatsApp connector:

```
WhatsApp msg ──► OpenWorker (Assistant session) ──► Claude Code session
Claude Code ──► OpenWorker ──────────────────────► WhatsApp reply
```

Sessions are addressed by free-form description ("the one working on the webhook"), not
by fixed names: the agent lists live sessions with transcript tails and decides which
one matches.

## Approach (chosen: A — tool kit on the OpenWorker agent)

No daemon, no new state. A new domain package plus three thin tools that the
WhatsApp-facing Assistant session gains. Inbound routing, session-per-contact, and the
platform reply path already exist (#23, #24, #29) and are not modified.

Rejected alternatives: a standing bridge daemon (more infra and state than the scenario
needs) and Claude Code hooks for push-based status (viable later as an incremental
improvement on top of this design, not as its base).

## Architecture

Two layers, dependencies pointing inward. Tools contain no logic; they only translate a
model call into domain calls.

```
coworker/claude_bridge/            # domain — no dependency on tools/engine
  models.py       # LiveSession dataclass
  discovery.py    # find live sessions (ps + lsof → cwd/tty → transcript)
  transcript.py   # read/watch the session JSONL
  terminal.py     # TerminalDriver protocol + ITerm2Driver (AppleScript)
coworker/tools/claude_sessions.py  # adapter: 3 tools, same shape as git_tools()
```

SOLID mapping:

- **S** — one responsibility per module: discovery knows nothing about terminals,
  transcript knows nothing about `ps`, terminal knows nothing about JSONL.
- **O/D** — `TerminalDriver` is a `Protocol` (`find_target(tty)`,
  `send_text(target, text)`). `ITerm2Driver` implements it via `osascript`. The tool
  layer depends on the protocol, so Terminal.app or tmux drivers can be added later
  without touching callers.
- **L** — every driver honours the same contract: return `None` for "not found" and
  `False` for a failed send; never raise for an absent target.
- **I** — each tool receives only what it uses: `find` gets the discovery, `send` gets
  the driver plus the transcript watcher. No god object.

Dependencies are injected at the factory:
`claude_session_tools(discovery, driver)` — tests pass fakes, `agent.py` passes real
implementations.

## Components

### `models.py`

```python
@dataclass
class LiveSession:
    pid: int
    tty: str                      # e.g. "ttys004" — the key used to find the iTerm tab
    cwd: str                      # the session's workspace
    branch: str | None            # git branch of cwd
    transcript: Path | None       # ~/.claude/projects/<munged>/<uuid>.jsonl
    transcript_confidence: str    # "matched" | "guessed" | "none"
    last_activity: datetime | None  # transcript mtime
    tail: str                     # last ~3 messages, summarised, for free-form matching
```

### `discovery.py` — `SessionDiscovery.list()`

1. `ps` finds `claude` processes (pid, tty, start time).
2. `lsof -p <pid>` yields the cwd.
3. cwd munged (`/` and `.` → `-`) → `~/.claude/projects/<munged>/`.
4. Transcript pick among the dir's `*.jsonl`: modified after the process started, most
   recent mtime. This is a documented heuristic; when more than one file qualifies the
   result carries `transcript_confidence: "guessed"` so the agent knows it can be wrong.
5. Builds each `LiveSession` with the transcript tail.

The subprocess runner is injectable (`run=subprocess.run`) so tests feed canned
`ps`/`lsof` output. Discovery excludes OpenWorker's own process tree so the agent can
never inject into the session that is answering WhatsApp.

### `transcript.py`

- `tail(path, n)` — last *n* parsed entries (role, text, timestamp). A malformed or
  truncated line (a write in progress) is skipped; parsing never raises.
- `wait_for_reply(path, after, timeout)` — polls mtime (1 s, injectable sleep); a new
  `assistant` entry with timestamp > `after` returns its text; timeout returns `None`.

### `terminal.py`

```python
class TerminalDriver(Protocol):
    def find_target(self, tty: str) -> str | None: ...   # opaque tab id
    def send_text(self, target: str, text: str) -> bool: ...
```

`ITerm2Driver`: `osascript` iterates windows/tabs/sessions, matches the `tty` property,
issues `write text`. Text is escaped (quotes, backslashes); multiline input is collapsed
to spaces because `write text` sends a trailing Enter. Failure returns `False`, never an
exception.

### `tools/claude_sessions.py`

Three tools, following the `git_tools()` factory-of-closures pattern:

- `find_claude_sessions()` → serialised `LiveSession` list. Free-form matching happens
  in the agent: it reads the tails and picks.
- `read_claude_transcript(tty, n=20)` → parsed tail.
- `send_to_claude_session(tty, text, wait_seconds=120)` → injects, waits for the reply
  via `wait_for_reply`, returns the reply text or `{"status": "sent_no_reply"}`.

`tty` is the identifier passed between tools — stable while the tab lives, produced by
`find`.

Registered in `agent.py` alongside the other `registry.register_all(...)` calls, only on
macOS.

## Flow

```
WhatsApp "did the webhook session finish?"
  → Evolution webhook → OpenWorker Assistant session (existing)
  → agent: find_claude_sessions() → reads tails → picks the match
  → read_claude_transcript(tty) → interprets state
  → answer → existing platform reply path (#24) → WhatsApp
```

With injection the agent calls `send_to_claude_session`; the tool blocks until a reply
or timeout, and the text goes back to WhatsApp. Long turns are fine — the WhatsApp
session already runs unattended.

## Error handling

| Failure | Behaviour |
|---|---|
| No live `claude` process | `find` returns an empty list plus a hint; the agent says so on WhatsApp |
| `ps`/`lsof` fails or hangs | 10 s timeout on the runner → `{"error": ...}`; a turn never stalls |
| cwd with no transcript yet (fresh session) | `LiveSession` with `transcript=None`, `tail=""` — still listed |
| Several candidate JSONLs | mtime heuristic; `transcript_confidence: "guessed"` marks the ambiguity |
| Tab closed between `find` and `send` | `find_target` → `None` → `{"error": "session_gone"}`; the agent re-runs `find` |
| Injection lands but Claude doesn't answer (long turn, or waiting on a permission prompt) | `wait_for_reply` timeout → `{"status": "sent_no_reply", "last_entries": tail}` — partial state, not silence |
| Malformed/truncated JSONL line | line skipped; parse never raises |
| Quotes/emoji/multiline in injected text | AppleScript escaping is tested; multiline collapses to spaces |
| Not macOS | tools are not registered at all |
| **Loop**: target session is the one answering WhatsApp | discovery excludes OpenWorker's own process tree |

## Testing

No test touches real `ps`, `lsof`, iTerm2, or `osascript` — everything is injected, so
the suite runs on any CI including Linux.

- `test_discovery.py` — fake runner with canned `ps`/`lsof` output: finds pids, maps the
  munged cwd, picks the right JSONL among several, excludes OpenWorker's own tree,
  yields `transcript=None` on an empty dir.
- `test_transcript.py` — real JSONL fixtures under `tmp_path`: tail parses roles, skips
  a malformed line; `wait_for_reply` sees a new entry and honours the timeout
  (injectable poll, no real sleeping).
- `test_terminal.py` — `ITerm2Driver` with a fake `osascript`: generates the right
  script, escapes quotes/backslashes/emoji, `find_target` returns `None` on no match,
  `send_text` returns `False` on failure.
- `test_claude_session_tools.py` — tools wired with fakes: every error contract from the
  table above (`session_gone`, `sent_no_reply`, empty list) plus serialisation.
