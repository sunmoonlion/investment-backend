"""Agent session execution extension over the template's single delivery policy."""

from __future__ import annotations

import uuid

from sqlalchemy import text

from app.infrastructure.agent.transactions import ExecutionLease, LeaseLost
from app.infrastructure.messaging.delivery_schedule import NOT_BEFORE_DUE_SQL
from app.infrastructure.messaging.durable_delivery import DurableDelivery

CONSUMER = "agent.executor"
TERMINAL = {"completed", "failed", "cancelled", "budget_exceeded"}


class AgentDelivery(DurableDelivery):
    active_execution_sql = """SELECT 1 FROM agent_execution_leases l
        WHERE l.command_id=m.id AND l.expires_at>clock_timestamp()"""

    def __init__(self, sessions, *, lease_seconds: int = 60, max_attempts: int = 10):
        super().__init__(
            sessions,
            topics={"agent.execution": CONSUMER, "agent.notification": None},
            lease_seconds=lease_seconds,
            max_attempts=max_attempts,
        )

    async def reconcile(self) -> int:
        count = await super().reconcile()
        # Domain-only recovery: transport policy does not know remote tool effects.
        async with self.sessions() as s, s.begin():
            await s.execute(
                text("""
                UPDATE tool_side_effects e SET status='unknown', updated_at=clock_timestamp()
                WHERE e.status='executing' AND NOT EXISTS (
                    SELECT 1 FROM agent_execution_leases l JOIN agent_runs r ON r.session_id=l.session_id
                    WHERE r.id=e.run_id AND l.epoch=e.execution_epoch AND l.expires_at>clock_timestamp())
            """)
            )
        return count

    async def claim_execution(self, command_id: uuid.UUID):
        owner = str(uuid.uuid4())
        async with self.sessions() as s, s.begin():
            result = await s.execute(
                text(f"""
                SELECT m.payload, r.id AS run_id, r.session_id, r.graph_name FROM outbox_message m
                JOIN agent_runs r ON r.id=CAST(m.aggregate_key AS uuid)
                WHERE m.id=:id AND m.topic='agent.execution'
                  AND {NOT_BEFORE_DUE_SQL}
                  AND NOT EXISTS (SELECT 1 FROM inbox_message i WHERE i.consumer=:consumer AND i.message_id=m.id)
                  AND NOT EXISTS (SELECT 1 FROM outbox_dead_letter f WHERE f.message_id=m.id AND f.replayed_at IS NULL)
            """),
                {"id": command_id, "consumer": CONSUMER},
            )
            row = result.mappings().first()
            if not row:
                return None
            payload = row["payload"]
            if payload.get("run_id") != str(row["run_id"]):
                raise ValueError("command run identity mismatch")
            if payload.get("operation") not in {"start", "resume"}:
                raise ValueError("unknown execution operation")
            graph = {"phase0": "walking_skeleton", "pilot": "research_web_pilot"}.get(
                payload.get("kind")
            )
            if graph != row["graph_name"]:
                raise ValueError("executor kind does not match the persisted run")
            result = await s.execute(
                text("""
                INSERT INTO agent_execution_leases(session_id,command_id,owner,epoch,expires_at)
                VALUES (:session,:command,:owner,1,clock_timestamp()+(:ttl * interval '1 second'))
                ON CONFLICT(session_id) DO UPDATE SET command_id=:command, owner=:owner,
                    epoch=agent_execution_leases.epoch+1,
                    expires_at=clock_timestamp()+(:ttl * interval '1 second')
                WHERE agent_execution_leases.expires_at<=clock_timestamp()
                RETURNING epoch
            """),
                {
                    "session": row["session_id"],
                    "command": command_id,
                    "owner": owner,
                    "ttl": self.lease_seconds,
                },
            )
            epoch = result.scalar_one_or_none()
            if epoch is None:
                return None
            # Recheck after the lease lock: a previous owner may have committed
            # its acknowledgement while we were waiting for that lock.
            done = await s.execute(
                text(
                    "select 1 from inbox_message where consumer=:c and message_id=:id"
                ),
                {"c": CONSUMER, "id": command_id},
            )
            if done.scalar_one_or_none():
                await s.execute(
                    text(
                        "update agent_execution_leases set expires_at='-infinity'::timestamptz where session_id=:id"
                    ),
                    {"id": row["session_id"]},
                )
                return None
            return ExecutionLease(row["session_id"], command_id, owner, epoch), payload

    async def renew(self, lease: ExecutionLease) -> None:
        async with self.sessions() as s, s.begin():
            result = await s.execute(
                text("""
                UPDATE agent_execution_leases SET expires_at=clock_timestamp()+(:ttl * interval '1 second')
                WHERE session_id=:session_id AND command_id=:command_id AND owner=:owner
                  AND epoch=:epoch AND expires_at>clock_timestamp() RETURNING epoch
            """),
                {**vars(lease), "ttl": self.lease_seconds},
            )
            if result.scalar_one_or_none() is None:
                raise LeaseLost("execution lease lost")

    async def release(self, lease: ExecutionLease) -> None:
        # Preserve the epoch tombstone without resurrecting a released owner
        # when the database wall clock moves backwards.
        async with self.sessions() as s, s.begin():
            await s.execute(
                text("""
                UPDATE agent_execution_leases SET expires_at='-infinity'::timestamptz
                WHERE session_id=:session_id AND command_id=:command_id
                  AND owner=:owner AND epoch=:epoch
            """),
                vars(lease),
            )
