# Claude Bridge Remote Approval (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "aprova"/"nega" from WhatsApp presses the right key in the waiting iTerm2 tab, behind a token-bound echo-confirm.

**Architecture:** `TerminalDriver` gains `send_keys` (keystroke without Enter, `write text ... newline NO`); `tools/claude_sessions.py` gains `respond_to_claude_prompt` implementing the stateless echo-confirm token (`sha256(session_id+"\n"+message)[:12]`), keystroke `1`/`3`, and transcript-mtime verification. Spec: `docs/superpowers/specs/2026-07-30-claude-bridge-remote-approval-design.md`.

**Tech Stack:** Python stdlib (`hashlib`); existing bridge modules; pytest.

## Global Constraints

- Hermetic tests only (fakes, `tmp_path`, injectable clock/sleep) — green on Linux CI.
- Driver contract unchanged: `False`/`None` for failure, never raises.
- Tool errors follow the established shapes: `{"error": ...}` / `{"status": ...}` dicts.
- Commits: imperative sentence, no `Co-Authored-By`. Tests via `source .venv/bin/activate`.

---

### Task 1: `send_keys` on the terminal driver

**Files:**
- Modify: `coworker/claude_bridge/terminal.py`
- Test: `tests/test_claude_bridge_terminal.py` (extend)

**Interfaces:**
- Produces: `TerminalDriver.send_keys(target: str, keys: str) -> bool`; `ITerm2Driver.send_keys` via `_SEND_KEYS` AppleScript (`write text "{keys}" newline NO`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_claude_bridge_terminal.py`:

```python
def test_send_keys_no_trailing_enter():
    fake = FakeOsascript(stdout="ok\n")
    driver = ITerm2Driver(run=fake)
    assert driver.send_keys("w0t0p0:ABC", "1") is True
    script = fake.scripts[0]
    assert 'write text "1" newline NO' in script
    assert '"w0t0p0:ABC"' in script


def test_send_keys_escapes():
    fake = FakeOsascript(stdout="ok\n")
    ITerm2Driver(run=fake).send_keys("id", '3"x')
    assert 'write text "3\\"x" newline NO' in fake.scripts[0]


def test_send_keys_false_on_missing_session_or_crash():
    assert ITerm2Driver(run=FakeOsascript(stdout="")).send_keys("id", "1") is False

    def broken(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)

    assert ITerm2Driver(run=broken).send_keys("id", "1") is False
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_claude_bridge_terminal.py -v` → FAIL (`send_keys` missing).

- [ ] **Step 3: Implement** — in `terminal.py`: add to the Protocol
`def send_keys(self, target: str, keys: str) -> bool: ...`; add module constant
`_SEND_KEYS` identical to `_SEND` but with `write text "{text}" newline NO`; add

```python
    def send_keys(self, target: str, keys: str) -> bool:
        script = _SEND_KEYS.format(target=_escape(target), text=_escape(keys))
        return self._osascript(script) == "ok"
```

- [ ] **Step 4: Run to verify pass** — same command, all PASS.
- [ ] **Step 5: Commit** — `git commit -m "Send a bare keystroke to an iTerm2 tab, without Enter"`

---

### Task 2: `respond_to_claude_prompt` tool

**Files:**
- Modify: `coworker/tools/claude_sessions.py`
- Test: `tests/test_claude_session_tools.py` (extend)

**Interfaces:**
- Consumes: `LiveSession.status`/`session_id`/`transcript` (phase 2), registry `SessionState.message` via a `prompt_message` lookup, `driver.send_keys` (Task 1).
- Produces: `respond_to_claude_prompt(tty, decision, confirm_token=None)` closure + `_RESPOND_SCHEMA`; factory gains `sleep`/`clock` injectables for the verify poll; token helper `_prompt_token(session_id, message) -> str`.
- Prompt message source: the tool needs the waiting prompt's text. `LiveSession` carries `status` but not `message`; read it via `read_sessions(bridge_dir)` — the factory receives the registry dir. Extend `claude_session_tools` with `bridge_dir: Path | None = None`; `claude_bridge_tools()` passes `default_bridge_dir()`. The tool is registered only when `bridge_dir` is provided (same conditional pattern as watch).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_claude_session_tools.py`:

```python
def _waiting_session(tmp_path, message="permission to run: git push",
                     session_id="sess-1", status="waiting_approval"):
    # As built: a local _write_bridge_state helper writes the registry file (tests/
    # is not a package, so importing another test module's fixture doesn't work).
    s = _session_with_id(session_id=session_id)
    s.status = status
    t = _write_transcript(tmp_path, ["before"])
    s.transcript = t
    _write_bridge_state(tmp_path, session_id, status=status, message=message,
                        transcript_path=str(t))
    return s


def _respond_tools(tmp_path, sessions, driver=None, clock=None, sleep=None):
    tools = claude_session_tools(
        FakeDiscovery(sessions),
        driver or FakeDriver(),
        bridge_dir=tmp_path,
        # As built: a counting clock — a constant clock would never reach the verify
        # deadline and the poll loop would spin forever.
        clock=clock or iter(range(1000)).__next__,
        sleep=sleep or (lambda s: None),
    )
    return {t.__name__: t for t in tools}


def test_respond_absent_without_bridge_dir():
    names = {t.__name__ for t in claude_session_tools(FakeDiscovery([]), FakeDriver())}
    assert "respond_to_claude_prompt" not in names


def test_respond_first_call_echoes_prompt_and_token(tmp_path):
    s = _waiting_session(tmp_path)
    t = _respond_tools(tmp_path, [s])
    result = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")
    assert result["status"] == "confirmation_required"
    assert result["prompt"] == "permission to run: git push"
    assert len(result["confirm_token"]) == 12


def test_respond_approve_with_token_presses_1_and_verifies(tmp_path):
    s = _waiting_session(tmp_path)
    driver = FakeDriver()
    ticks = iter([0.0, 1.0, 2.0, 3.0])
    mtimes = {"n": 0}

    def clock():
        return next(ticks)

    def sleep(_):
        # first poll: bump the transcript so mtime advances
        s.transcript.write_text(
            s.transcript.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )

    t = _respond_tools(tmp_path, [s], driver=driver, clock=clock, sleep=sleep)
    token = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")[
        "confirm_token"
    ]
    result = t["respond_to_claude_prompt"](
        tty="ttys000", decision="approve", confirm_token=token
    )
    assert result["status"] == "approved"
    assert result["verified"] is True
    assert driver.keys == [("w0t0p0:ABC", "1")]


def test_respond_deny_presses_3(tmp_path):
    s = _waiting_session(tmp_path)
    driver = FakeDriver()
    t = _respond_tools(tmp_path, [s], driver=driver)
    token = t["respond_to_claude_prompt"](tty="ttys000", decision="deny")[
        "confirm_token"
    ]
    result = t["respond_to_claude_prompt"](
        tty="ttys000", decision="deny", confirm_token=token
    )
    assert result["status"] in ("denied", "sent_unverified")
    assert driver.keys == [("w0t0p0:ABC", "3")]


def test_respond_stale_token_is_prompt_changed(tmp_path):
    s = _waiting_session(tmp_path)
    t = _respond_tools(tmp_path, [s])
    result = t["respond_to_claude_prompt"](
        tty="ttys000", decision="approve", confirm_token="deadbeef0000"
    )
    assert result["error"] == "prompt_changed"
    assert len(result["confirm_token"]) == 12


def test_respond_not_waiting_reports_current_status(tmp_path):
    s = _waiting_session(tmp_path, status="idle")
    t = _respond_tools(tmp_path, [s])
    result = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")
    assert result == {"error": "not_waiting", "current_status": "idle"}


def test_respond_no_registry(tmp_path):
    s = _session_with_id(session_id=None)
    t = _respond_tools(tmp_path, [s])
    result = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")
    assert result["error"] == "no_registry"


def test_respond_unverified_when_transcript_frozen(tmp_path):
    s = _waiting_session(tmp_path)
    t = _respond_tools(
        tmp_path, [s], clock=iter(range(100)).__next__, sleep=lambda _: None
    )
    token = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")[
        "confirm_token"
    ]
    result = t["respond_to_claude_prompt"](
        tty="ttys000", decision="approve", confirm_token=token
    )
    assert result["status"] == "sent_unverified"


def test_respond_invalid_arguments(tmp_path):
    s = _waiting_session(tmp_path)
    t = _respond_tools(tmp_path, [s])
    assert t["respond_to_claude_prompt"](tty="", decision="approve") == {
        "error": "invalid_arguments"
    }
    assert t["respond_to_claude_prompt"](tty="ttys000", decision="shrug") == {
        "error": "invalid_arguments"
    }


def test_respond_session_gone_and_send_failed(tmp_path):
    s = _waiting_session(tmp_path)
    t = _respond_tools(tmp_path, [s], driver=FakeDriver(target=None))
    token_result = t["respond_to_claude_prompt"](tty="ttys000", decision="approve")
    result = t["respond_to_claude_prompt"](
        tty="ttys000", decision="approve",
        confirm_token=token_result["confirm_token"],
    )
    assert result == {"error": "session_gone"}

    t2 = _respond_tools(tmp_path, [s], driver=FakeDriver(send_ok=False))
    token = t2["respond_to_claude_prompt"](tty="ttys000", decision="approve")[
        "confirm_token"
    ]
    assert t2["respond_to_claude_prompt"](
        tty="ttys000", decision="approve", confirm_token=token
    ) == {"error": "send_failed"}
```

Also update `FakeDriver` with:

```python
    def send_keys(self, target: str, keys: str) -> bool:
        self.keys.append((target, keys))
        return self.send_ok
```

(`self.keys: list = []` in `__init__`), and `_write_state` in the registry test module
must accept `message`/`transcript_path` overrides (it already takes `**overrides`).

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_claude_session_tools.py -v` → FAIL.

- [ ] **Step 3: Implement** — in `tools/claude_sessions.py`:

Imports: `import hashlib`, `import time`, `from ..claude_bridge.registry import read_sessions`.

```python
def _prompt_token(session_id: str, message: str) -> str:
    return hashlib.sha256(f"{session_id}\n{message}".encode()).hexdigest()[:12]
```

Schema:

```python
_RESPOND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "respond_to_claude_prompt",
        "description": (
            "Approve or deny the permission prompt a Claude Code session is waiting "
            "on (by `tty`). PROTOCOL: call once WITHOUT confirm_token — you get the "
            "exact prompt text and a token; show the prompt to the user verbatim and "
            "wait for their explicit confirmation; only then call again WITH the "
            "token. Never pass the token without the user's explicit confirmation, "
            "and never fabricate one. decision='approve' presses 1, 'deny' presses 3."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tty": {"type": "string", "description": "The session's tty."},
                "decision": {
                    "type": "string",
                    "enum": ["approve", "deny"],
                    "description": "What to do with the waiting prompt.",
                },
                "confirm_token": {
                    "type": "string",
                    "description": (
                        "Token from the confirmation_required response — only after "
                        "the user explicitly confirmed."
                    ),
                },
            },
            "required": ["tty", "decision"],
        },
    },
}
```

Factory signature gains `bridge_dir: Optional[Path] = None`, `sleep: Callable[[float], None] = time.sleep`, `clock: Callable[[], float] = time.monotonic`. Closure:

```python
    def respond_to_claude_prompt(
        tty: str, decision: str, confirm_token: Optional[str] = None
    ) -> dict[str, Any]:
        if (
            not isinstance(tty, str)
            or not tty.strip()
            or decision not in ("approve", "deny")
        ):
            return {"error": "invalid_arguments"}
        session = _by_tty(tty)
        if session is None:
            return {"error": "session_gone"}
        if session.session_id is None:
            return {
                "error": "no_registry",
                "hint": (
                    "This session has no hook registry entry — install the bridge "
                    "hooks with: python -m coworker.claude_bridge.install"
                ),
            }
        if session.status != "waiting_approval":
            return {"error": "not_waiting", "current_status": session.status}
        assert bridge_dir is not None
        states = {s.session_id: s for s in read_sessions(bridge_dir)}
        state = states.get(session.session_id)
        message = (state.message if state else None) or ""
        prompt = message or "a permission request"
        expected = _prompt_token(session.session_id, message)
        if confirm_token is None:
            return {
                "status": "confirmation_required",
                "prompt": prompt,
                "confirm_token": expected,
            }
        if confirm_token != expected:
            return {
                "error": "prompt_changed",
                "prompt": prompt,
                "confirm_token": expected,
            }
        target = driver.find_target(session.tty)
        if target is None:
            return {"error": "session_gone"}
        before = None
        if session.transcript is not None:
            try:
                before = session.transcript.stat().st_mtime
            except OSError:
                before = None
        key = "1" if decision == "approve" else "3"
        if not driver.send_keys(target, key):
            return {"error": "send_failed"}
        verified = False
        if before is not None:
            deadline = clock() + 10.0
            while clock() < deadline:
                sleep(1.0)
                try:
                    if session.transcript.stat().st_mtime > before:
                        verified = True
                        break
                except OSError:
                    break
        if not verified:
            return {"status": "sent_unverified", "decision": decision, "prompt": prompt}
        return {
            "status": "approved" if decision == "approve" else "denied",
            "verified": True,
            "prompt": prompt,
        }
```

Registration block becomes:

```python
    tools = [find_claude_sessions, read_claude_transcript, send_to_claude_session]
    if watches is not None:
        watch_claude_session.__coworker_schema__ = _WATCH_SCHEMA
        unwatch_claude_session.__coworker_schema__ = _UNWATCH_SCHEMA
        tools += [watch_claude_session, unwatch_claude_session]
    if bridge_dir is not None:
        respond_to_claude_prompt.__coworker_schema__ = _RESPOND_SCHEMA
        tools.append(respond_to_claude_prompt)
    return tools
```

`claude_bridge_tools()` passes `bridge_dir=default_bridge_dir()`. Update the factory-names test to expect six tools.

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_claude_session_tools.py -v`, then full suite `pytest -q`.
- [ ] **Step 5: Commit** — `git commit -m "Approve or deny a waiting permission prompt from chat, echo-confirmed"`
