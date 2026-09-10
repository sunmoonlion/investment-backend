"""Agent-owned delivery policy over the shared transactional outbox primitive."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from app.infrastructure.agent.transactions import ExecutionLease, LeaseLost

CONSUMER = "agent.executor"
TERMINAL = {"completed", "failed", "cancelled", "budget_exceeded"}


class AgentDelivery:
    def __init__(self, sessions, *, lease_seconds: int = 60, max_attempts: int = 10):
        self.sessions = sessions
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    async def claim_delivery(self) -> dict[str, Any] | None:
        owner = str(
            uuid.uuid4()
        )  # unique for each claim, including a retry by the same process
        async with self.sessions() as s, s.begin():
            result = await s.execute(
                text("""
                WITH candidate AS (
                    SELECT m.id FROM outbox_message m
                    WHERE m.topic IN ('agent.execution', 'agent.notification')
                      AND m.available_at <= clock_timestamp()
                      AND (m.status = 'pending' OR (m.status = 'delivering' AND m.lease_expires_at < clock_timestamp()))
                      AND NOT EXISTS (SELECT 1 FROM agent_delivery_failures f WHERE f.message_id=m.id AND f.replayed_at IS NULL)
                    ORDER BY m.created_at, m.id FOR UPDATE OF m SKIP LOCKED LIMIT 1
                )
                UPDATE outbox_message m SET status='delivering', lease_owner=:owner,
                    lease_expires_at=clock_timestamp()+(:ttl * interval '1 second'),
                    attempt_count=attempt_count+1, updated_at=clock_timestamp()
                FROM candidate c WHERE m.id=c.id RETURNING m.*
            """),
                {"owner": owner, "ttl": self.lease_seconds},
            )
            row = result.mappings().first()
            return dict(row) if row else None

    async def finish_delivery(self, message, *, error: str | None = None) -> None:
        async with self.sessions() as s, s.begin():
            result = await s.execute(
                text("""
                UPDATE outbox_message SET status=:status,
                    published_at=CASE WHEN :ok THEN clock_timestamp() ELSE published_at END,
                    lease_owner=NULL, lease_expires_at=NULL, last_error=:error,
                    available_at=clock_timestamp()+(:delay * interval '1 second'), updated_at=clock_timestamp()
                WHERE id=:id AND lease_owner=:owner AND status='delivering'
                  AND lease_expires_at > clock_timestamp() RETURNING id
            """),
                {
                    "id": message["id"],
                    "owner": message["lease_owner"],
                    "status": "pending" if error else "published",
                    "ok": error is None,
                    "error": error,
                    "delay": min(300, 2 ** min(message["attempt_count"], 8)),
                },
            )
            if result.scalar_one_or_none() is None:
                raise LeaseLost("delivery lease lost")
            if error and message["attempt_count"] >= self.max_attempts:
                await s.execute(
                    text("""
                    INSERT INTO agent_delivery_failures(message_id,error_code) VALUES (:id,:error)
                    ON CONFLICT(message_id) DO UPDATE SET error_code=:error,
                        failed_at=clock_timestamp(), replayed_at=NULL
                """),
                    {"id": message["id"], "error": error},
                )

    async def reconcile(self) -> int:
        # Broker acceptance is not executor acceptance. Recover published commands
        # with neither a committed inbox acknowledgement nor a live execution.
        async with self.sessions() as s, s.begin():
            await s.execute(
                text("""
                INSERT INTO agent_delivery_failures(message_id,error_code)
                SELECT m.id, 'execution_unacknowledged' FROM outbox_message m
                WHERE m.topic='agent.execution' AND m.status='published'
                  AND m.attempt_count>=:attempts
                  AND m.published_at<clock_timestamp()-(:ttl * interval '1 second')
                  AND NOT EXISTS(SELECT 1 FROM inbox_message i WHERE i.consumer=:consumer AND i.message_id=m.id)
                  AND NOT EXISTS(SELECT 1 FROM agent_execution_leases l WHERE l.command_id=m.id AND l.expires_at>clock_timestamp())
                ON CONFLICT(message_id) DO UPDATE SET error_code='execution_unacknowledged',
                    failed_at=clock_timestamp(), replayed_at=NULL
            """),
                {
                    "attempts": self.max_attempts,
                    "ttl": self.lease_seconds,
                    "consumer": CONSUMER,
                },
            )
            result = await s.execute(
                text("""
                UPDATE outbox_message m SET status='pending', available_at=clock_timestamp(),
                    updated_at=clock_timestamp()
                WHERE m.topic='agent.execution' AND m.status='published'
                  AND m.published_at < clock_timestamp()-(:ttl * interval '1 second')
                  AND NOT EXISTS (SELECT 1 FROM inbox_message i WHERE i.consumer=:consumer AND i.message_id=m.id)
                  AND NOT EXISTS (SELECT 1 FROM agent_execution_leases l WHERE l.command_id=m.id AND l.expires_at>clock_timestamp())
                  AND NOT EXISTS (SELECT 1 FROM agent_delivery_failures f WHERE f.message_id=m.id AND f.replayed_at IS NULL)
                RETURNING m.id
            """),
                {"ttl": self.lease_seconds, "consumer": CONSUMER},
            )
            count = len(result.all())
            # A process killed after sending a remote effect leaves executing.
            # Once its epoch has expired it is unknown, never silently retryable.
            await s.execute(
                text("""
                UPDATE tool_side_effects e SET status='unknown', updated_at=clock_timestamp()
                WHERE e.status='executing' AND NOT EXISTS (
                    SELECT 1 FROM agent_execution_leases l JOIN agent_runs r ON r.session_id=l.session_id
                    WHERE r.id=e.run_id AND l.epoch=e.execution_epoch AND l.expires_at>clock_timestamp())
            """)
            )
            return count

    async def replay(self, message_id: uuid.UUID) -> None:
        async with self.sessions() as s, s.begin():
            found = await s.execute(
                text("""
                UPDATE agent_delivery_failures SET replayed_at=clock_timestamp()
                WHERE message_id=:id AND replayed_at IS NULL RETURNING message_id
            """),
                {"id": message_id},
            )
            if found.scalar_one_or_none() is None:
                raise ValueError("message is not in the Agent dead-letter journal")
            await s.execute(
                text("""
                UPDATE outbox_message SET status='pending', attempt_count=0,
                    available_at=clock_timestamp(), last_error=NULL,
                    lease_owner=NULL, lease_expires_at=NULL WHERE id=:id
            """),
                {"id": message_id},
            )

    async def claim_execution(self, command_id: uuid.UUID):
        owner = str(uuid.uuid4())
        async with self.sessions() as s, s.begin():
            result = await s.execute(
                text("""
                SELECT m.payload, r.id AS run_id, r.session_id, r.graph_name FROM outbox_message m
                JOIN agent_runs r ON r.id=CAST(m.aggregate_key AS uuid)
                WHERE m.id=:id AND m.topic='agent.execution'
                  AND NOT EXISTS (SELECT 1 FROM inbox_message i WHERE i.consumer=:consumer AND i.message_id=m.id)
                  AND NOT EXISTS (SELECT 1 FROM agent_delivery_failures f WHERE f.message_id=m.id AND f.replayed_at IS NULL)
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
                        "update agent_execution_leases set expires_at=clock_timestamp() where session_id=:id"
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
        async with self.sessions() as s, s.begin():
            await s.execute(
                text("""
                UPDATE agent_execution_leases SET expires_at=clock_timestamp()
                WHERE session_id=:session_id AND owner=:owner AND epoch=:epoch
            """),
                vars(lease),
            )
