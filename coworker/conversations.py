"""ConversationStore — global, file-backed session storage shared by all surfaces.

Layout under a base dir (default `~/.config/coworker/`):
  coworker.db                  SQLite index: sessions(id → project, title, n_msgs), workspaces, memory
  conversations/<id>.jsonl     append-only message log, one file per conversation

Writes append only the new messages each turn (no rewriting history). Legacy rows that
stored messages inline are lazily migrated to a .jsonl on first load/save.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .sessions import SessionRecord

# A session id becomes a filename (`<id>.jsonl`) and a scratch dir name, so it must be a
# single, benign path component. Every legitimate id is hex or a `__run__`/`__task__`-
# prefixed hex string, so this charset is a superset of what we generate; it excludes the
# path separators and dots (`/`, `\`, `..`) a client-supplied id would need to escape the
# store. Session ids arrive from client-controlled surfaces (the `/ws/session/{id}` route,
# REST paths), so without this an id like `../../evil` writes `<base>/evil.jsonl` outside
# `conversations/`.
_SAFE_SESSION_ID = re.compile(r"\A[A-Za-z0-9_-]{1,128}\Z")


def is_safe_session_id(sid: str) -> bool:
    return bool(isinstance(sid, str) and _SAFE_SESSION_ID.match(sid))


def _load_roots(raw: Optional[str]) -> list[dict]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _load_json_dict(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _display_title(row: sqlite3.Row) -> Optional[str]:
    """Title precedence for every read path: a manual rename (renamed=1) always wins,
    then the generated auto_title, then the first-line snapshot `save()` wrote."""
    if row["renamed"]:
        return row["title"]
    return row["auto_title"] or row["title"]


def title_from(messages: list[dict]) -> str:
    from .attachments import content_to_text

    for m in messages:
        if m.get("role") == "user":
            text = content_to_text(m.get("content"), image_placeholder="").strip()
            if text:
                return text.splitlines()[0][:60]
    return "New session"


class ConversationStore:
    def __init__(self, base_dir: str | Path) -> None:
        self.base = Path(base_dir).expanduser()
        self.base.mkdir(parents=True, exist_ok=True)
        self.conv_dir = self.base / "conversations"
        self.conv_dir.mkdir(exist_ok=True)
        self.db_path = self.base / "coworker.db"

        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY, workspace TEXT, model TEXT, mode TEXT,
                title TEXT, agent TEXT DEFAULT 'code', n_msgs INTEGER DEFAULT 0, messages TEXT,
                extra_roots TEXT, pinned INTEGER DEFAULT 0, archived INTEGER DEFAULT 0,
                origin TEXT, origin_label TEXT,
                auto_title TEXT, renamed INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS workspaces (
                path TEXT PRIMARY KEY, last_used TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """)
        for ddl in (
            "ALTER TABLE sessions ADD COLUMN title TEXT",
            "ALTER TABLE sessions ADD COLUMN n_msgs INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN agent TEXT DEFAULT 'code'",
            "ALTER TABLE sessions ADD COLUMN extra_roots TEXT",
            "ALTER TABLE sessions ADD COLUMN pinned INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN archived INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN origin TEXT",
            "ALTER TABLE sessions ADD COLUMN origin_label TEXT",
            "ALTER TABLE sessions ADD COLUMN auto_title TEXT",
            "ALTER TABLE sessions ADD COLUMN renamed INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN grants TEXT",
            "ALTER TABLE sessions ADD COLUMN compaction TEXT",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        self._conn.commit()
        self._backfill_counts()

    # -- file helpers -----------------------------------------------------------
    def _file(self, sid: str) -> Path:
        # Single chokepoint for every conversation-file path. Reject ids that aren't a
        # safe path component, then confirm the resolved path stays inside conv_dir — so
        # a crafted id can never read or clobber a file outside the store.
        if not is_safe_session_id(sid):
            raise ValueError(f"unsafe session id: {sid!r}")
        path = (self.conv_dir / f"{sid}.jsonl").resolve()
        if path.parent != self.conv_dir.resolve():
            raise ValueError(f"unsafe session id: {sid!r}")
        return path

    def _read_jsonl_lines(self, sid: str) -> tuple[Optional[list[dict]], int]:
        """Parse the log. Returns (messages, dropped): `dropped` is how many lines were
        skipped as invalid JSON. (None, 0) when there is no file yet."""
        path = self._file(sid)
        if not path.exists():
            return None, 0
        # Tolerate a corrupt/truncated line rather than failing the whole load. An append
        # interrupted mid-write (crash, disk full) leaves one malformed trailing line; a
        # bare `json.loads` in a comprehension would raise JSONDecodeError and make load()
        # throw every time thereafter — bricking that session on every surface that opens
        # it. Skip the bad line(s) and keep the recoverable history. (Every other JSON read
        # in this module is already tolerant; this one was the outlier.)
        # Read bytes and decode per line. A torn write can cut inside a multi-byte
        # character (an emoji at the end of a DM is four bytes). A strict text read of
        # the whole file raised UnicodeDecodeError before any line was parsed, and
        # that bricked load() the same way a bare json.loads did.
        messages: list[dict] = []
        dropped = 0
        for line in path.read_bytes().split(b"\n"):
            if not line.strip():
                continue
            try:
                messages.append(json.loads(line.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError):
                dropped += 1
        return messages, dropped

    def _read_jsonl(self, sid: str) -> Optional[list[dict]]:
        return self._read_jsonl_lines(sid)[0]

    # -- tool-call/result pairing repair ---------------------------------------
    @staticmethod
    def _repair_tool_pairing(messages: list[dict]) -> list[dict]:
        """Reorder messages so every tool result immediately follows its call.

        Append-only persistence means an interrupted turn can leave a user
        message between an assistant ``tool_calls`` block and the matching
        ``tool`` result.  Providers reject this ordering (Anthropic 400/2013,
        OpenAI "tool_call_ids did not have response messages"), making the
        session permanently unrecoverable.

        This pass:
        * Moves a real ``tool`` result found later in the thread to sit right
          after its call.
        * Synthesises a placeholder result for a call with no matching tool
          message — but **only** when the thread has moved past the call
          (i.e. there are messages after the assistant block).  A trailing
          assistant ``tool_calls`` with no result is a pending/interrupted
          call that the engine will resume; injecting a placeholder there
          would break durable resume.
        * Is idempotent — a well-formed thread passes through unchanged.
        """
        if not messages:
            return messages

        # Collect tool_call ids from assistant messages.
        pending_calls: dict[str, int] = {}  # call_id → index of the assistant msg
        for i, m in enumerate(messages):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    call_id = tc.get("id")
                    if call_id:
                        pending_calls[call_id] = i

        if not pending_calls:
            return messages  # no tool calls at all

        # Find tool results and where they sit relative to their calls.
        # call_id → index of the tool result message (if found)
        found_results: dict[str, int] = {}
        for i, m in enumerate(messages):
            if m.get("role") == "tool":
                call_id = m.get("tool_call_id")
                if call_id and call_id in pending_calls:
                    # Only keep the first result for each call.
                    if call_id not in found_results:
                        found_results[call_id] = i

        # Determine which calls are "trailing" — the assistant block is the
        # last message in the thread (nothing after it).  These are pending
        # calls that the engine will resume; we must not inject placeholders.
        last_msg_idx = len(messages) - 1
        trailing_calls: set[str] = set()
        for call_id, call_idx in pending_calls.items():
            if call_idx == last_msg_idx:
                trailing_calls.add(call_id)

        # Calls that have a result already immediately following the assistant
        # message are fine — no work needed.  We only need to act when a result
        # is missing or out-of-order.  Trailing calls without results are
        # skipped (they're pending, not corrupt).
        needs_repair = False
        for call_id, call_idx in pending_calls.items():
            if call_id in trailing_calls and call_id not in found_results:
                continue  # pending call — engine will resume
            if call_id in found_results:
                result_idx = found_results[call_id]
                if result_idx != call_idx + 1:
                    needs_repair = True  # result exists but not immediately after
            else:
                needs_repair = True  # no result at all
        if not needs_repair:
            return messages  # already well-formed (or only pending calls)

        # Build the repaired list.  We iterate through the original messages,
        # and after each assistant message we emit its tool results (moved from
        # their original position or synthesised if missing).
        consumed_result_indices: set[int] = set()
        repaired: list[dict] = []

        for i, m in enumerate(messages):
            if m.get("role") == "assistant" and m.get("tool_calls"):
                repaired.append(m)
                # Emit results for each tool call in this block, in order.
                for tc in m["tool_calls"]:
                    call_id = tc.get("id")
                    if not call_id:
                        continue
                    if call_id in found_results:
                        result_idx = found_results[call_id]
                        if result_idx not in consumed_result_indices:
                            repaired.append(messages[result_idx])
                            consumed_result_indices.add(result_idx)
                    elif call_id not in trailing_calls:
                        # Synthesise a placeholder so the thread is well-formed.
                        # Skip trailing calls — they're pending, not corrupt.
                        repaired.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": '{"error": "tool result was lost during an interrupted turn"}',
                        })
            elif i in consumed_result_indices:
                continue  # already moved this tool result up
            else:
                repaired.append(m)

        return repaired

    def _count(self, sid: str) -> int:
        path = self._file(sid)
        if not path.exists():
            return 0
        # Bytes, not text: a torn tail is not always valid UTF-8, and a count must
        # never raise. Same reason as _read_jsonl_lines.
        return sum(1 for line in path.read_bytes().split(b"\n") if line.strip())

    @staticmethod
    def _ends_without_newline(path: Path) -> bool:
        if not path.exists() or path.stat().st_size == 0:
            return False
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            return f.read(1) != b"\n"

    def _append(self, sid: str, messages: list[dict]) -> None:
        path = self._file(sid)
        # A torn last line (write cut mid-record, no newline) would swallow the next
        # record into itself and both would be lost on load. Close it first. save()
        # no longer appends onto a torn tail, but _backfill_counts still can.
        needs_newline = self._ends_without_newline(path)
        with open(path, "a", encoding="utf-8") as f:
            if needs_newline:
                f.write("\n")
            for m in messages:
                f.write(json.dumps(m) + "\n")

    def _rewrite(self, sid: str, messages: list[dict]) -> None:
        # Atomic rewrite: write the full log to a temp file, then replace in one
        # step. An in-place open(..., "w") truncates the file immediately, so a
        # crash mid-rewrite would erase the conversation history (same
        # tmp-then-replace pattern as subscriptions.ChannelBuffer._save).
        path = self._file(sid)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for m in messages:
                f.write(json.dumps(m) + "\n")
        tmp.replace(path)

    def _backfill_counts(self) -> None:
        """One-time per session: move any inline blob into a .jsonl and persist
        title + n_msgs in the index. Skips already-migrated rows on later startups."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, messages, n_msgs, title FROM sessions"
            ).fetchall()
            for row in rows:
                sid = row["session_id"]
                jsonl = self._file(sid)
                if jsonl.exists() and row["title"] and row["n_msgs"]:
                    continue  # already migrated
                if jsonl.exists():
                    messages = self._read_jsonl(sid) or []
                elif row["messages"]:
                    try:
                        messages = json.loads(row["messages"])
                    except json.JSONDecodeError:
                        messages = []
                    if messages:
                        self._append(sid, messages)
                    self._conn.execute(
                        "UPDATE sessions SET messages = NULL WHERE session_id = ?",
                        (sid,),
                    )
                else:
                    messages = []
                self._conn.execute(
                    "UPDATE sessions SET n_msgs = ?, title = ? WHERE session_id = ?",
                    (len(messages), row["title"] or title_from(messages), sid),
                )
            self._conn.commit()

    # -- API --------------------------------------------------------------------
    def save(self, record: SessionRecord) -> None:
        sid = record.session_id
        with self._lock:
            # lazily migrate a legacy inline blob into the .jsonl
            if not self._file(sid).exists():
                row = self._conn.execute(
                    "SELECT messages FROM sessions WHERE session_id = ?", (sid,)
                ).fetchone()
                if row and row["messages"]:
                    try:
                        legacy = json.loads(row["messages"])
                    except json.JSONDecodeError:
                        legacy = []
                    if legacy:
                        self._append(sid, legacy)

            if self._ends_without_newline(self._file(sid)):
                # A torn tail (write cut mid-record, disk full for a moment) makes the
                # line count a lie. Counting the torn line as a slot appended only what
                # came after it: a checkpoint of [A(tool_calls), T] that tore inside A
                # left T on disk with no call, and the provider rejects that (400) in
                # a way the pairing repair cannot fix. The engine's list is the truth
                # here. Write it whole.
                self._rewrite(sid, record.messages)
            else:
                existing = self._count(sid)
                if len(record.messages) > existing:
                    self._append(sid, record.messages[existing:])
                elif len(record.messages) < existing:  # rare; not append-only
                    self._rewrite(sid, record.messages)

            title = record.title or title_from(record.messages)
            self._conn.execute(
                """
                INSERT INTO sessions (session_id, workspace, model, mode, title, agent, n_msgs, messages, extra_roots, grants, compaction, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    workspace = excluded.workspace, model = excluded.model, mode = excluded.mode,
                    title = COALESCE(sessions.title, excluded.title), agent = excluded.agent,
                    n_msgs = excluded.n_msgs, messages = NULL, extra_roots = excluded.extra_roots,
                    grants = excluded.grants, compaction = excluded.compaction,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    sid,
                    record.workspace,
                    record.model,
                    record.mode,
                    title,
                    record.agent,
                    len(record.messages),
                    json.dumps(record.extra_roots or []),
                    json.dumps(record.grants or {}),
                    json.dumps(record.compaction or {}),
                ),
            )
            self._conn.commit()
        self.touch_workspace(record.workspace)

    def load(self, session_id: str) -> Optional[SessionRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if not row:
                return None
            raw, dropped = self._read_jsonl_lines(session_id)
            if raw is None:
                try:
                    raw = json.loads(row["messages"] or "[]")
                except json.JSONDecodeError:
                    raw = []
            # Self-heal: ensure every tool result immediately follows its call.
            # An interrupted turn can persist a user message between an assistant
            # tool_calls block and its tool result, which providers reject (400).
            messages = self._repair_tool_pairing(raw)
            # save() appends by line count. So the list we hand back must match the
            # disk line for line, or the next save duplicates (placeholder inserted)
            # or loses (corrupt line dropped) messages. Rewrite when they diverge.
            # Also materialises a legacy blob, so save() sees a file and skips the
            # blob migration.
            # Compare content, not identity. The repair pass builds a fresh list for
            # a well-formed block with two or more calls (the second result sits at
            # call index + 2, which it reads as out of place). load() runs on every
            # inbound and every inbox poll. Identity as the signal meant one rewrite
            # per read for any session that ever ran two tools in one step.
            if dropped > 0 or messages != raw:
                self._rewrite(session_id, messages)
                # The session list reads n_msgs from the index. Keep it in step with
                # the file now. Waiting for the next save leaves a stale count.
                self._conn.execute(
                    "UPDATE sessions SET n_msgs = ? WHERE session_id = ?",
                    (len(messages), session_id),
                )
                self._conn.commit()
        return SessionRecord(
            session_id=session_id,
            workspace=row["workspace"],
            model=row["model"],
            mode=row["mode"],
            messages=messages,
            title=_display_title(row),
            agent=row["agent"] or "code",
            message_count=len(messages),
            updated_at=row["updated_at"],
            extra_roots=_load_roots(
                row["extra_roots"] if "extra_roots" in row.keys() else None
            ),
            grants=_load_json_dict(row["grants"] if "grants" in row.keys() else None),
            # Auto-compaction state (OPE-27) — same defensive parse as grants.
            compaction=_load_json_dict(
                row["compaction"] if "compaction" in row.keys() else None
            ),
            pinned=bool(row["pinned"]),
            archived=bool(row["archived"]),
            origin=row["origin"],
            origin_label=row["origin_label"],
        )

    def set_extra_roots(self, session_id: str, extra_roots: list[dict]) -> None:
        """Persist just the session's added folders, independent of its message log — used when
        the user adds/removes a folder (which may happen with no active engine)."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET extra_roots = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (json.dumps(extra_roots or []), session_id),
            )
            self._conn.commit()

    def list(self, *, workspace: Optional[str] = None) -> list[SessionRecord]:
        with self._lock:
            if workspace is None:
                rows = self._conn.execute(
                    "SELECT * FROM sessions ORDER BY pinned DESC, updated_at DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM sessions WHERE workspace = ? ORDER BY pinned DESC, updated_at DESC",
                    (workspace,),
                ).fetchall()
        return [
            SessionRecord(
                session_id=r["session_id"],
                workspace=r["workspace"],
                model=r["model"],
                mode=r["mode"],
                messages=[],
                title=_display_title(r),
                agent=r["agent"] or "code",
                message_count=r["n_msgs"] or 0,
                updated_at=r["updated_at"],
                pinned=bool(r["pinned"]),
                archived=bool(r["archived"]),
                origin=r["origin"],
                origin_label=r["origin_label"],
            )
            for r in rows
        ]

    def touch_workspace(self, path: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO workspaces (path, last_used) VALUES (?, CURRENT_TIMESTAMP) "
                "ON CONFLICT(path) DO UPDATE SET last_used = CURRENT_TIMESTAMP",
                (path,),
            )
            self._conn.commit()

    def recent_workspaces(self, limit: int = 20) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT path FROM workspaces ORDER BY last_used DESC LIMIT ?", (limit,)
            ).fetchall()
        return [r["path"] for r in rows]

    def canonicalize_workspaces(self) -> None:
        with self._lock:
            for (ws,) in self._conn.execute(
                "SELECT DISTINCT workspace FROM sessions WHERE workspace IS NOT NULL"
            ).fetchall():
                real = os.path.realpath(ws)
                if real != ws:
                    self._conn.execute(
                        "UPDATE sessions SET workspace = ? WHERE workspace = ?",
                        (real, ws),
                    )
            latest: dict[str, str] = {}
            for path, last in self._conn.execute(
                "SELECT path, last_used FROM workspaces"
            ).fetchall():
                real = os.path.realpath(path)
                if real not in latest or (last or "") > latest[real]:
                    latest[real] = last
            self._conn.execute("DELETE FROM workspaces")
            for path, last in latest.items():
                self._conn.execute(
                    "INSERT OR REPLACE INTO workspaces (path, last_used) VALUES (?, ?)",
                    (path, last),
                )
            self._conn.commit()

    def delete(self, session_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()
            # Row and file go together, under the same lock. load() writes now (the
            # pairing repair rewrites the file) and save() re-creates the row. With
            # the unlink outside the lock, a save between the two steps could leave
            # a row whose file was then removed underneath it.
            path = self._file(session_id)
            if path.exists():
                path.unlink()
        return cur.rowcount > 0

    def rename(self, session_id: str, title: str) -> bool:
        clean = " ".join((title or "").split())[:120]
        if not clean:
            return False
        with self._lock:
            # renamed=1 makes the manual title final: auto-titling skips the session and
            # `_display_title` ignores any auto_title already there.
            cur = self._conn.execute(
                "UPDATE sessions SET title = ?, renamed = 1, updated_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (clean, session_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def set_auto_title(self, session_id: str, title: str) -> bool:
        """Store a generated title. Its own column — never `title` — so a manual rename
        (past or future) always wins; doesn't touch updated_at (a title landing after the
        turn must not reorder the session list)."""
        clean = " ".join((title or "").split())[:60]
        if not clean:
            return False
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET auto_title = ? WHERE session_id = ? AND renamed = 0",
                (clean, session_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def title_state(self, session_id: str) -> Optional[dict]:
        """The auto-title guard inputs: whether the user renamed and whether a generated
        title already exists. None when the session has no row yet."""
        with self._lock:
            row = self._conn.execute(
                "SELECT renamed, auto_title FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return {"renamed": bool(row["renamed"]), "auto_title": row["auto_title"]}

    def set_flags(
        self,
        session_id: str,
        *,
        pinned: Optional[bool] = None,
        archived: Optional[bool] = None,
    ) -> bool:
        """Update pin/archive flags without touching updated_at (so pinning doesn't reorder)."""
        sets, params = [], []
        if pinned is not None:
            sets.append("pinned = ?")
            params.append(1 if pinned else 0)
        if archived is not None:
            sets.append("archived = ?")
            params.append(1 if archived else 0)
        if not sets:
            return False
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE session_id = ?",
                (*params, session_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def set_origin(self, session_id: str, origin: str, origin_label: str = "") -> bool:
        """Mark where a spawned session came from (§31). Set once at spawn; `save()` never
        names these columns, so per-turn saves can't clobber them (the pinned mechanism).
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sessions SET origin = ?, origin_label = ? WHERE session_id = ?",
                (origin, origin_label or None, session_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        self._conn.close()
