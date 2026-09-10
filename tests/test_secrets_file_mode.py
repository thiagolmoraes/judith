"""Secrets must never touch the disk world-readable, even briefly (#143).

The write used to be: create the temp with `Path.write_text` (umask default, 0644 on a
normal box), then `chmod 0600`, then rename. The plaintext existed at 0644 for the length
of the write, which is readable by every other local process.
"""

import json
import os
import stat
import sys

import pytest

from coworker import secrets as secrets_mod
from coworker.secrets import SecretStore, write_private_text

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX mode bits; Windows uses the icacls ACL path"
)


@posix_only
def test_written_secret_file_is_user_only(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("openai", {"api_key": "sk-live-secret"})
    assert stat.S_IMODE((tmp_path / "secrets.json").stat().st_mode) == 0o600


@posix_only
def test_write_private_text_is_user_only(tmp_path):
    path = write_private_text(tmp_path / "token.txt", "sk-live-secret")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@posix_only
def test_temp_file_is_never_group_or_world_readable_mid_write(tmp_path, monkeypatch):
    """The regression itself.

    Hooks `_restrict_to_user`, which both the old and the new writer call, and samples the
    temp's mode at that moment. Old order was write_text (umask default) -> chmod 0600 ->
    rename, so the plaintext was on disk at 0644 first and this observes it. New order
    creates the file 0600 and empty, so the same sample sees 0600.
    """
    observed = {}
    real = secrets_mod._restrict_to_user

    def spy(path, *, is_dir):
        if not is_dir and path.exists():
            observed[path.name] = stat.S_IMODE(path.stat().st_mode)
        return real(path, is_dir=is_dir)

    monkeypatch.setattr(secrets_mod, "_restrict_to_user", spy)
    SecretStore(tmp_path / "secrets.json").put("openai", {"api_key": "sk-live"})

    assert observed, "expected the writer to restrict a temp file"
    for name, mode in observed.items():
        assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0, (
            f"{name} existed at {oct(mode)} while holding the plaintext"
        )


def test_no_temp_file_is_left_behind(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("openai", {"api_key": "sk-live"})
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "secrets.json"]
    assert leftovers == []


def test_content_round_trips(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    store.put("openai", {"api_key": "sk-live"})
    store.put("anthropic", {"api_key": "sk-ant"})
    on_disk = json.loads((tmp_path / "secrets.json").read_text())
    assert on_disk["openai"]["api_key"] == "sk-live"
    assert on_disk["anthropic"]["api_key"] == "sk-ant"
    assert SecretStore(tmp_path / "secrets.json").get("openai")["api_key"] == "sk-live"


def test_a_failed_write_leaves_the_previous_file_intact(tmp_path, monkeypatch):
    path = tmp_path / "secrets.json"
    store = SecretStore(path)
    store.put("openai", {"api_key": "sk-original"})

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(secrets_mod.os, "replace", boom)
    with pytest.raises(OSError):
        store.put("openai", {"api_key": "sk-replacement"})

    assert json.loads(path.read_text())["openai"]["api_key"] == "sk-original"
    assert [p.name for p in tmp_path.iterdir()] == ["secrets.json"], "temp must be cleaned up"


def test_a_hostile_preexisting_temp_name_cannot_redirect_the_write(tmp_path):
    """The old fixed `<name>.tmp` was predictable; a symlink there redirected the write."""
    victim = tmp_path / "victim.txt"
    victim.write_text("do not clobber")
    decoy = tmp_path / "secrets.json.tmp"
    try:
        decoy.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    SecretStore(tmp_path / "secrets.json").put("openai", {"api_key": "sk-live"})
    assert victim.read_text() == "do not clobber"


def test_a_failed_restrict_closes_the_fd_and_leaves_no_temp(tmp_path, monkeypatch):
    """`_restrict_to_user` used to run before the fd was wrapped, so a chmod failure
    leaked the descriptor. `put` runs on every provider save, so the leak added up."""
    fds: list[int] = []
    real_mkstemp = secrets_mod.tempfile.mkstemp

    def capturing_mkstemp(*a, **kw):
        fd, name = real_mkstemp(*a, **kw)
        fds.append(fd)
        return fd, name

    def refuse_file(path, *, is_dir):
        if not is_dir:
            raise OSError("chmod refused")

    monkeypatch.setattr(secrets_mod.tempfile, "mkstemp", capturing_mkstemp)
    monkeypatch.setattr(secrets_mod, "_restrict_to_user", refuse_file)

    with pytest.raises(OSError):
        write_private_text(tmp_path / "token.txt", "sk-live")

    assert len(fds) == 1
    with pytest.raises(OSError):
        os.fstat(fds[0])  # closed, not leaked
    assert list(tmp_path.iterdir()) == []


def test_the_temp_is_synced_before_it_replaces_the_target(tmp_path, monkeypatch):
    synced: list[int] = []
    sync_seen_at_replace = {"value": False}
    real_fsync = os.fsync
    real_replace = os.replace

    def spy_fsync(fd):
        synced.append(fd)
        return real_fsync(fd)

    def spy_replace(src, dst):
        sync_seen_at_replace["value"] = bool(synced)
        return real_replace(src, dst)

    monkeypatch.setattr(secrets_mod.os, "fsync", spy_fsync)
    monkeypatch.setattr(secrets_mod.os, "replace", spy_replace)

    path = write_private_text(tmp_path / "token.txt", "sk-live")

    assert len(synced) == 1
    assert sync_seen_at_replace["value"] is True
    assert path.read_text() == "sk-live"
