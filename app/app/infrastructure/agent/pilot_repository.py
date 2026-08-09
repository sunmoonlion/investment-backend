from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.runtime import validate_run_status_transition
from app.infrastructure.graph.pilot_graph import (
    PILOT_GRAPH_NAME,
    PILOT_GRAPH_VERSION,
)


class PilotRepository:
    """P0-008C durable projection repository.

    It intentionally uses pilot-specific request/control journals while
    reusing the existing Research session/run/event truth tables.  M1 may
    replace this repository without migrating stable traffic.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_run(
        self,
        *,
        owner_actor_id: uuid.UUID,
        idempotency_key: uuid.UUID,
        title: str,
        user_input: str,
    ) -> tuple[dict[str, Any], bool]:
        lock_key = f"{owner_actor_id}:{idempotency_key}"
        await self.session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": lock_key},
        )
        existing = await self.session.execute(
            text(
                """
                select r.id, r.session_id, r.status, p.title, p.user_input,
                       r.created_at, r.updated_at
                from agent_pilot_requests p
                join agent_runs r on r.id = p.run_id
                where p.owner_actor_id = :owner_actor_id
                  and p.idempotency_key = :idempotency_key
                """
            ),
            {
                "owner_actor_id": owner_actor_id,
                "idempotency_key": idempotency_key,
            },
        )
        row = existing.mappings().first()
        if row:
            return dict(row), False

        session_id = uuid.uuid4()
        run_id = uuid.uuid4()
        await self.session.execute(
            text(
                """
                insert into agent_sessions (id, status, owner_actor_id)
                values (:id, 'created', :owner_actor_id)
                """
            ),
            {"id": session_id, "owner_actor_id": owner_actor_id},
        )
        await self.session.execute(
            text(
                """
                insert into agent_runs (
                    id, session_id, graph_name, graph_version,
                    agent_profile_key, agent_profile_version, thread_id,
                    idempotency_key, status
                )
                values (
                    :id, :session_id, :graph_name, :graph_version,
                    'literature_review', 1, :thread_id,
                    :idempotency_key, 'created'
                )
                """
            ),
            {
                "id": run_id,
                "session_id": session_id,
                "graph_name": PILOT_GRAPH_NAME,
                "graph_version": PILOT_GRAPH_VERSION,
                "thread_id": str(session_id),
                "idempotency_key": str(idempotency_key),
            },
        )
        await self.session.execute(
            text(
                """
                insert into agent_pilot_requests (
                    id, owner_actor_id, idempotency_key, run_id, title, user_input
                )
                values (
                    :id, :owner_actor_id, :idempotency_key, :run_id,
                    :title, :user_input
                )
                """
            ),
            {
                "id": uuid.uuid4(),
                "owner_actor_id": owner_actor_id,
                "idempotency_key": idempotency_key,
                "run_id": run_id,
                "title": title,
                "user_input": user_input,
            },
        )
        await self.session.execute(
            text(
                """
                insert into agent_pilot_controls (run_id)
                values (:run_id)
                """
            ),
            {"run_id": run_id},
        )
        await self.session.commit()
        return (
            {
                "id": run_id,
                "session_id": session_id,
                "status": "created",
                "title": title,
                "user_input": user_input,
            },
            True,
        )

    async def get_run(
        self, *, run_id: uuid.UUID, owner_actor_id: uuid.UUID
    ) -> dict[str, Any] | None:
        result = await self.session.execute(
            text(
                """
                select r.id, r.session_id, r.thread_id, r.status, r.resume_token,
                       r.error, r.created_at, r.updated_at, p.title, p.user_input,
                       c.cancel_requested, c.resume_action_id,
                       c.resume_idempotency_key
                from agent_runs r
                join agent_sessions s on s.id = r.session_id
                join agent_pilot_requests p on p.run_id = r.id
                join agent_pilot_controls c on c.run_id = r.id
                where r.id = :run_id and s.owner_actor_id = :owner_actor_id
                """
            ),
            {"run_id": run_id, "owner_actor_id": owner_actor_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def get_run_for_worker(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        result = await self.session.execute(
            text(
                """
                select r.id, r.session_id, r.thread_id, r.status, r.resume_token,
                       r.error, r.created_at, r.updated_at, p.owner_actor_id,
                       p.title, p.user_input, c.cancel_requested,
                       c.resume_action_id, c.resume_idempotency_key
                from agent_runs r
                join agent_pilot_requests p on p.run_id = r.id
                join agent_pilot_controls c on c.run_id = r.id
                where r.id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def set_status(
        self,
        *,
        run_id: uuid.UUID,
        session_id: uuid.UUID,
        status: str,
        resume_token: str | None = None,
        error: str | None = None,
    ) -> None:
        result = await self.session.execute(
            text("select status from agent_runs where id = :run_id for update"),
            {"run_id": run_id},
        )
        current = result.scalar_one_or_none()
        if current is None:
            raise ValueError("pilot run not found")
        validate_run_status_transition(str(current), status)
        await self.session.execute(
            text(
                """
                update agent_runs
                set status = :status,
                    resume_token = :resume_token,
                    error = :error,
                    updated_at = now()
                where id = :run_id
                """
            ),
            {
                "run_id": run_id,
                "status": status,
                "resume_token": resume_token,
                "error": error,
            },
        )
        await self.session.execute(
            text(
                """
                update agent_sessions
                set status = :status, updated_at = now()
                where id = :session_id
                """
            ),
            {"session_id": session_id, "status": status},
        )
        await self.session.commit()

    async def append_browser_event(
        self,
        *,
        run_id: uuid.UUID,
        session_id: uuid.UUID,
        event_type: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        await self.session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:session_id, 0))"),
            {"session_id": str(session_id)},
        )
        db_sequence = await self.session.execute(
            text(
                """
                select coalesce(max(sequence_no), 0) + 1
                from session_events where session_id = :session_id
                """
            ),
            {"session_id": session_id},
        )
        browser_sequence = await self.session.execute(
            text(
                """
                select coalesce(max((payload->>'sequence_no')::integer), 0) + 1
                from session_events
                where run_id = :run_id and event_type = 'BrowserRunEvent'
                """
            ),
            {"run_id": run_id},
        )
        event_id = uuid.uuid4()
        now = datetime.now().astimezone()
        payload = {
            "contract_version": 1,
            "event_id": str(event_id),
            "run_id": str(run_id),
            "sequence_no": int(browser_sequence.scalar_one()),
            "occurred_at": now.isoformat(),
            "type": event_type,
            "data": data,
        }
        await self.session.execute(
            text(
                """
                insert into session_events (
                    id, session_id, run_id, sequence_no, category, event_type,
                    payload_schema_version, lineage, payload, metadata
                )
                values (
                    :id, :session_id, :run_id, :sequence_no, 'ui',
                    'BrowserRunEvent', 1, cast(:lineage as jsonb),
                    cast(:payload as jsonb), '{}'::jsonb
                )
                """
            ),
            {
                "id": event_id,
                "session_id": session_id,
                "run_id": run_id,
                "sequence_no": int(db_sequence.scalar_one()),
                "lineage": json.dumps(
                    {
                        "session_id": str(session_id),
                        "run_id": str(run_id),
                        "root_run_id": str(run_id),
                    }
                ),
                "payload": json.dumps(payload),
            },
        )
        await self.session.commit()
        return payload

    async def list_browser_events(
        self,
        *,
        run_id: uuid.UUID,
        owner_actor_id: uuid.UUID,
        after_event_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        run = await self.get_run(run_id=run_id, owner_actor_id=owner_actor_id)
        if run is None:
            raise PermissionError("pilot run belongs to another actor")
        after_sequence = 0
        if after_event_id is not None:
            cursor_result = await self.session.execute(
                text(
                    """
                    select (payload->>'sequence_no')::integer
                    from session_events
                    where id = :event_id
                      and run_id = :run_id
                      and event_type = 'BrowserRunEvent'
                    """
                ),
                {"event_id": after_event_id, "run_id": run_id},
            )
            cursor = cursor_result.scalar_one_or_none()
            if cursor is None:
                raise ValueError("pilot event cursor is invalid")
            after_sequence = int(cursor)
        result = await self.session.execute(
            text(
                """
                select payload
                from session_events
                where run_id = :run_id
                  and event_type = 'BrowserRunEvent'
                  and (payload->>'sequence_no')::integer > :after_sequence
                order by (payload->>'sequence_no')::integer asc
                """
            ),
            {"run_id": run_id, "after_sequence": after_sequence},
        )
        return [dict(row[0]) for row in result.all()]

    async def request_cancel(
        self,
        *,
        run_id: uuid.UUID,
        owner_actor_id: uuid.UUID,
    ) -> dict[str, Any]:
        run = await self.get_run(run_id=run_id, owner_actor_id=owner_actor_id)
        if run is None:
            raise PermissionError("pilot run belongs to another actor")
        if run["status"] in {"completed", "failed", "cancelled"}:
            return run
        await self.session.execute(
            text(
                """
                update agent_pilot_controls
                set cancel_requested = true, updated_at = now()
                where run_id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        await self.session.commit()
        await self.set_status(
            run_id=run_id,
            session_id=run["session_id"],
            status="cancelled",
        )
        return (await self.get_run(run_id=run_id, owner_actor_id=owner_actor_id)) or run

    async def consume_resume(
        self,
        *,
        run_id: uuid.UUID,
        owner_actor_id: uuid.UUID,
        action_id: uuid.UUID,
        idempotency_key: uuid.UUID,
    ) -> tuple[dict[str, Any], bool]:
        await self.session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"pilot-resume:{run_id}"},
        )
        run = await self.get_run(run_id=run_id, owner_actor_id=owner_actor_id)
        if run is None:
            raise PermissionError("pilot run belongs to another actor")
        existing_key = run.get("resume_idempotency_key")
        if existing_key is not None:
            if uuid.UUID(str(existing_key)) == idempotency_key:
                return run, False
            raise ValueError("pilot resume was already consumed")
        if run["status"] != "waiting":
            raise ValueError("pilot run is not waiting for input")
        if str(run.get("resume_token") or "") != str(action_id):
            raise ValueError("pilot action is stale")
        await self.session.execute(
            text(
                """
                update agent_pilot_controls
                set resume_action_id = :action_id,
                    resume_idempotency_key = :idempotency_key,
                    updated_at = now()
                where run_id = :run_id
                  and resume_idempotency_key is null
                """
            ),
            {
                "run_id": run_id,
                "action_id": action_id,
                "idempotency_key": idempotency_key,
            },
        )
        await self.session.commit()
        return run, True

    async def assert_citation_owner(
        self, *, evidence_id: uuid.UUID, owner_actor_id: uuid.UUID
    ) -> None:
        result = await self.session.execute(
            text(
                """
                select 1
                from session_events e
                join agent_sessions s on s.id = e.session_id
                join agent_pilot_requests p on p.run_id = e.run_id
                where s.owner_actor_id = :owner_actor_id
                  and e.event_type = 'BrowserRunEvent'
                  and e.payload->>'type' = 'citation'
                  and e.payload->'data'->'citation'->>'evidence_id' = :evidence_id
                limit 1
                """
            ),
            {
                "owner_actor_id": owner_actor_id,
                "evidence_id": str(evidence_id),
            },
        )
        if result.scalar_one_or_none() is None:
            raise PermissionError("pilot citation belongs to another actor")
