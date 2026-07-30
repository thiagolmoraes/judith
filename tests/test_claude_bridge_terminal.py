"""ITerm2Driver — fake osascript runner; asserts on the generated AppleScript."""

from __future__ import annotations

import subprocess

from coworker.claude_bridge.terminal import ITerm2Driver


class FakeOsascript:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
        self.scripts: list[str] = []

    def __call__(self, cmd, **kwargs):
        assert cmd[0] == "osascript" and cmd[1] == "-e"
        assert kwargs.get("timeout") == 10
        self.scripts.append(cmd[2])
        return subprocess.CompletedProcess(
            cmd, self.returncode, stdout=self.stdout, stderr=""
        )


def test_find_target_matches_tty_with_dev_prefix():
    fake = FakeOsascript(stdout="w0t0p0:ABC\n")
    driver = ITerm2Driver(run=fake)
    assert driver.find_target("ttys004") == "w0t0p0:ABC"
    assert '"/dev/ttys004"' in fake.scripts[0]


def test_find_target_none_when_no_match_or_failure():
    assert ITerm2Driver(run=FakeOsascript(stdout="")).find_target("ttys004") is None
    assert (
        ITerm2Driver(run=FakeOsascript(stdout="x", returncode=1)).find_target("ttys004")
        is None
    )


def test_find_target_survives_osascript_crash():
    def broken(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)

    assert ITerm2Driver(run=broken).find_target("ttys004") is None


def test_send_text_escapes_and_collapses():
    fake = FakeOsascript(stdout="ok\n")
    driver = ITerm2Driver(run=fake)
    assert driver.send_text("w0t0p0:ABC", 'say "hi"\\now\nplease') is True
    script = fake.scripts[0]
    assert '\\"hi\\"' in script  # quotes escaped
    assert "\\\\now" in script  # literal backslash doubled
    assert 'write text "say \\"hi\\"\\\\now please"' in script  # one line, fully escaped


def test_send_text_multiline_becomes_single_line():
    fake = FakeOsascript(stdout="ok\n")
    ITerm2Driver(run=fake).send_text("id", "line one\nline two")
    assert 'write text "line one line two"' in fake.scripts[0]


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


def test_send_text_false_on_missing_session_or_failure():
    assert ITerm2Driver(run=FakeOsascript(stdout="")).send_text("id", "x") is False
    assert (
        ITerm2Driver(run=FakeOsascript(stdout="ok", returncode=1)).send_text("id", "x")
        is False
    )
