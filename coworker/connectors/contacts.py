"""Contact lookup, provider-agnostic.

Authorizing someone who has never written in means naming them first, and the owner
knows a name, not a phone number. That lookup is a *capability* — WhatsApp through
Evolution has it today, another backend may have it tomorrow — so consumers depend on
this contract, never on the implementation. `whatsapp.py` owns the only one that knows
Evolution exists, keeping that server confined to that module as its header promises.

Nothing here persists: contacts are read on demand for one search and returned. The
only thing that outlives the request is what the owner explicitly authorizes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, runtime_checkable

from .phone import format_phone, normalize_phone


@dataclass(frozen=True)
class Contact:
    number: str  # bare digits — the same key the allow-list stores
    name: Optional[str] = None


@runtime_checkable
class ContactDirectory(Protocol):
    """A searchable address book. `search` never raises: a backend that is down or
    unsupported returns an empty list, because a failed lookup must not break the page
    that also lets the owner type a number by hand."""

    def search(self, query: str, limit: int = 20) -> list[Contact]: ...

    def available(self) -> bool:
        """Whether a lookup can work at all (configured + reachable in principle)."""
        ...


def matches(contact: Contact, query: str) -> bool:
    """Name substring (case-insensitive) or number substring (digits only), so both
    "ana" and a pasted "+55 11 99999-9999" find the same person."""
    if not query:
        return True
    q = query.strip().lower()
    if contact.name and q in contact.name.lower():
        return True
    digits = "".join(ch for ch in query if ch.isdigit())
    return bool(digits) and digits in contact.number


def search_contacts(
    directory: Optional[ContactDirectory],
    query: str,
    *,
    allowed: Iterable[str],
    limit: int = 20,
) -> list[dict]:
    """Directory + allow-list → rows for the UI: the stored key, a readable form, and
    whether this person is already authorized (so the picker offers no duplicates)."""
    if directory is None:
        return []
    allowed_set = {normalize_phone(a) or a for a in allowed}
    return [
        {
            "number": c.number,
            "name": c.name,
            "display": format_phone(c.number),
            "allowed": c.number in allowed_set,
        }
        for c in directory.search(query, limit=limit)
    ]
