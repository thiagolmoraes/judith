"""Persistent memory — adapter interface + scopes.

Memory is the long-lived layer above transient conversation state: durable facts,
preferences, task notes, summaries. Scopes: global (user-wide) and workspace (per
project). Backends are adapters (`SQLiteMemoryStore` now, `PostgresMemoryStore` later).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Scope(str, Enum):
    # SESSION existed here for a while but no tool or surface could ever create one —
    # per-session state lives in the conversation itself, not in durable memory.
    GLOBAL = "global"
    WORKSPACE = "workspace"


@dataclass
class MemoryItem:
    id: int
    scope: Scope
    content: str
    workspace: Optional[str] = None
    created_at: Optional[str] = None
    # Set by update(); None means the memory still reads as originally written. The
    # prompt guidance tells the model memories "reflect when they were written" — this
    # is the timestamp that backs that up.
    updated_at: Optional[str] = None


class MemoryStore(ABC):
    @abstractmethod
    def add(
        self,
        content: str,
        *,
        scope: Scope = Scope.WORKSPACE,
        workspace: Optional[str] = None,
    ) -> MemoryItem: ...

    @abstractmethod
    def get(self, item_id: int) -> Optional[MemoryItem]: ...

    @abstractmethod
    def list(
        self,
        *,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
    ) -> list[MemoryItem]: ...

    @abstractmethod
    def update(self, item_id: int, content: str) -> Optional[MemoryItem]: ...

    @abstractmethod
    def delete(self, item_id: int) -> bool: ...

    @abstractmethod
    def search(
        self,
        query: str,
        *,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
        limit: int = 20,
    ) -> list[MemoryItem]: ...

    @abstractmethod
    def recent(
        self,
        *,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
        limit: int = 30,
    ) -> list[MemoryItem]: ...

    @abstractmethod
    def count(
        self,
        *,
        scope: Optional[Scope] = None,
        workspace: Optional[str] = None,
    ) -> int: ...


def format_memories(items: list[MemoryItem], *, omitted: int = 0) -> str:
    """Render memories for injection into the system prompt. Ids are shown so the agent
    can revise a memory (`memory_update`) or retire it (`memory_forget`). When the
    caller capped the list, `omitted` says how many older ones stayed out — the note
    points the agent at `memory_search` instead of pretending they don't exist."""
    if not items:
        return ""
    lines = [f"- [#{item.id}] {item.content}" for item in items]
    block = "Known memories (from earlier sessions):\n" + "\n".join(lines)
    if omitted > 0:
        block += (
            f"\n({omitted} older memories not shown — use memory_search to find them.)"
        )
    return block
