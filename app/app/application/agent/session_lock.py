from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SessionLockToken:
    session_id: str
    owner: str
    value: str


class SessionLock(Protocol):
    async def acquire(self, *, session_id: str, owner: str) -> SessionLockToken | None:
        """Return a token only when this owner acquired the session lock."""

    async def renew(self, token: SessionLockToken) -> bool:
        """Extend the lock TTL when the token still owns the lock."""
        ...

    async def release(self, token: SessionLockToken) -> None:
        """Release the lock if it is still owned by the supplied token."""
