"""SQLite-backed memory store (the default adapter)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .base import MemoryItem, MemoryStore, Scope


class SQLiteMemoryStore(MemoryStore):
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the server runs the WS handler on a different thread
        # than the store was created on; a lock serializes access.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL,
                key TEXT,
                content TEXT NOT NULL,
                workspace TEXT,
                session_id TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """)
        self._conn.commit()

    def add(
        self,
        content: str,
        *,
        scope: Scope = Scope.WORKSPACE,
        key: Optional[str] = None,
        workspace: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> MemoryItem:
        scope = Scope(scope)
        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO memories (scope, key, content, workspace, session_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (scope.value, key, content, workspace, session_id),
            )
            self._conn.commit()
            item = self.get(cursor.lastrowid)
        assert item is not None
        return item

    def get(self, item_id: int) -> Optional[MemoryItem]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id = ?", (item_id,)
            ).fetchone()
        return _row_to_item(row) if row else None

    def list(
        self,
        *,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[MemoryItem]:
        query = "SELECT * FROM memories WHERE 1 = 1"
        params: list[object] = []
        if scope is not None:
            query += " AND scope = ?"
            params.append(Scope(scope).value)
        if workspace is not None:
            query += " AND workspace = ?"
            params.append(workspace)
        if session_id is not None:
            query += " AND session_id = ?"
            params.append(session_id)
        query += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_row_to_item(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
        limit: int = 20,
    ) -> list[MemoryItem]:
        # LIKE is enough at this scale (hundreds of rows); newest first so the most
        # recent take on a topic wins the limit. ESCAPE so a literal % or _ in the
        # query can't blow the match wide open.
        pattern = (
            "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        sql = "SELECT * FROM memories WHERE content LIKE ? ESCAPE '\\'"
        params: list[object] = [pattern]
        if scope is not None:
            sql += " AND scope = ?"
            params.append(Scope(scope).value)
        if workspace is not None:
            sql += " AND workspace = ?"
            params.append(workspace)
        # max(0, …): SQLite treats LIMIT -1 as no limit — a negative limit must mean
        # "nothing", never "everything".
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(0, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_item(row) for row in rows]

    def recent(
        self,
        *,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
        limit: int = 30,
    ) -> list[MemoryItem]:
        sql = "SELECT * FROM memories WHERE 1 = 1"
        params: list[object] = []
        if scope is not None:
            sql += " AND scope = ?"
            params.append(Scope(scope).value)
        if workspace is not None:
            sql += " AND workspace = ?"
            params.append(workspace)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(0, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_item(row) for row in rows]

    def count(
        self,
        *,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
    ) -> int:
        sql = "SELECT COUNT(*) FROM memories WHERE 1 = 1"
        params: list[object] = []
        if scope is not None:
            sql += " AND scope = ?"
            params.append(Scope(scope).value)
        if workspace is not None:
            sql += " AND workspace = ?"
            params.append(workspace)
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row[0])

    def update(self, item_id: int, content: str) -> Optional[MemoryItem]:
        with self._lock:
            self._conn.execute(
                "UPDATE memories SET content = ? WHERE id = ?", (content, item_id)
            )
            self._conn.commit()
        return self.get(item_id)

    def delete(self, item_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM memories WHERE id = ?", (item_id,))
            self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()


def _row_to_item(row: sqlite3.Row) -> MemoryItem:
    return MemoryItem(
        id=row["id"],
        scope=Scope(row["scope"]),
        content=row["content"],
        key=row["key"],
        workspace=row["workspace"],
        session_id=row["session_id"],
        created_at=row["created_at"],
    )
