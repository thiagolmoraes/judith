"""Type into a terminal tab. TerminalDriver is the seam: iTerm2 today, Terminal.app or
tmux later, without the tool layer changing.

Contract every driver honours: `find_target` returns None for "not found" (tab closed,
app not running) and `send_text` returns False on failure — neither ever raises for an
absent target. iTerm2 is driven via `osascript`; `write text` appends Enter, so
multiline input is collapsed to one line (a literal newline would submit each line as a
separate prompt).
"""

from __future__ import annotations

import subprocess
from typing import Callable, Protocol


class TerminalDriver(Protocol):
    def find_target(self, tty: str) -> str | None: ...

    def send_text(self, target: str, text: str) -> bool: ...

    def send_keys(self, target: str, keys: str) -> bool: ...


_FIND = """\
tell application "iTerm2"
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if tty of s is "{tty}" then return id of s
      end repeat
    end repeat
  end repeat
end tell
return ""
"""

_SEND = """\
tell application "iTerm2"
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if id of s is "{target}" then
          tell s to write text "{text}"
          return "ok"
        end if
      end repeat
    end repeat
  end repeat
end tell
return ""
"""


# Same as _SEND but without the trailing Enter (`newline NO`) — a permission prompt
# reacts to the bare number key, and an Enter would land in the main input box.
_SEND_KEYS = """\
tell application "iTerm2"
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if id of s is "{target}" then
          tell s to write text "{text}" newline NO
          return "ok"
        end if
      end repeat
    end repeat
  end repeat
end tell
return ""
"""


def _escape(text: str) -> str:
    """Into an AppleScript string literal: backslashes first, then quotes; newlines
    collapse to spaces (write text sends Enter — a newline would submit early)."""
    flat = " ".join(text.splitlines())
    return flat.replace("\\", "\\\\").replace('"', '\\"')


class ITerm2Driver:
    def __init__(
        self, *, run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    ) -> None:
        self._run = run

    def find_target(self, tty: str) -> str | None:
        dev = tty if tty.startswith("/dev/") else f"/dev/{tty}"
        out = self._osascript(_FIND.format(tty=_escape(dev)))
        return out or None

    def send_text(self, target: str, text: str) -> bool:
        script = _SEND.format(target=_escape(target), text=_escape(text))
        return self._osascript(script) == "ok"

    def send_keys(self, target: str, keys: str) -> bool:
        script = _SEND_KEYS.format(target=_escape(target), text=_escape(keys))
        return self._osascript(script) == "ok"

    def _osascript(self, script: str) -> str | None:
        try:
            out = self._run(
                ["osascript", "-e", script], capture_output=True, text=True, timeout=10
            )
        except Exception:
            return None
        if out.returncode != 0:
            return None
        return out.stdout.strip()
