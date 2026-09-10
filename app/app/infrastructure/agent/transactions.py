"""Agent transaction ownership and persistent execution fencing."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import wraps
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.outbox import OutboxEvent
from app.infrastructure.repositories.outbox import SqlOutboxRepository


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionLease:
    session_id: UUID
    command_id: UUID
    owner: str
    epoch: int


def atomic(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        async with self.transaction():
            return await method(self, *args, **kwargs)

    return wrapped


class AgentTransactions:
    def __init__(self, session: AsyncSession, *, lease: ExecutionLease | None = None):
        self.session = session
        self.lease = lease
        self._transaction_depth = 0

    @asynccontextmanager
    async def transaction(self):
        outer = self._transaction_depth == 0
        self._transaction_depth += 1
        try:
            if outer and self.lease:
                await self.assert_lease()
            yield
            if outer:
                if self.lease:
                    await self.assert_lease()
                await self.session.commit()
        except BaseException:
            if outer:
                await self.session.rollback()
            raise
        finally:
            self._transaction_depth -= 1

    async def assert_lease(self) -> None:
        if self.lease is None:
            raise LeaseLost("an execution lease is required")
        row = await self.session.execute(
            text("""
            SELECT epoch FROM agent_execution_leases
            WHERE session_id = :session_id AND command_id = :command_id
              AND owner = :owner AND epoch = :epoch
              AND expires_at > clock_timestamp()
            FOR UPDATE
        """),
            vars(self.lease),
        )
        if row.scalar_one_or_none() is None:
            raise LeaseLost("execution lease expired, cancelled or superseded")

    async def enqueue(
        self, *, topic: str, key: str, run_id: str, payload: dict[str, Any]
    ) -> UUID:
        return await SqlOutboxRepository().enqueue(
            self.session,
            OutboxEvent(
                topic=topic,
                aggregate_key=str(run_id),
                deduplication_key=key,
                payload=payload,
            ),
        )

    async def notify(
        self, *, key: str, run_id: str, channel: str, payload: dict[str, Any]
    ) -> UUID:
        return await self.enqueue(
            topic="agent.notification",
            key=key,
            run_id=run_id,
            payload={"channel": channel, "message": payload},
        )

    async def acknowledge(self, command_id: UUID) -> None:
        if self.lease is None or self.lease.command_id != command_id:
            raise LeaseLost("command acknowledgement requires its execution lease")
        await SqlOutboxRepository.claim_inbox_once(
            self.session, consumer="agent.executor", message_id=command_id
        )

    async def save_execution(self, run_id: str, snapshot: dict[str, Any]) -> None:
        import json

        await self.assert_lease()
        assert self.lease is not None
        await self.session.execute(
            text("""
            UPDATE agent_runs SET execution_state = CAST(:state AS jsonb)
            WHERE id = :id AND session_id = :session_id
        """),
            {
                "id": run_id,
                "session_id": self.lease.session_id,
                "state": json.dumps(snapshot),
            },
        )
