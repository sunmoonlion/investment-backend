from __future__ import annotations

import uuid
import json
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.models import DomainEvent, RunLineage, UIEvent
from app.domain.agent.runtime import validate_run_status_transition


class AgentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_session(self) -> str:
        session_id = str(uuid.uuid4())
        await self.session.execute(
            text(
                """
                insert into agent_sessions (id, status)
                values (:id, 'created')
                """
            ),
            {"id": session_id},
        )
        await self.session.commit()
        return session_id

    async def create_run(
        self,
        *,
        session_id: str,
        idempotency_key: str | None,
        agent_profile_key: str | None,
        agent_profile_version: int = 1,
    ) -> dict[str, Any]:
        if idempotency_key:
            existing = await self.session.execute(
                text(
                    """
                    select id, session_id, status
                    from agent_runs
                    where session_id = :session_id and idempotency_key = :idempotency_key
                    """
                ),
                {"session_id": session_id, "idempotency_key": idempotency_key},
            )
            row = existing.mappings().first()
            if row:
                return dict(row)

        run_id = str(uuid.uuid4())
        await self.session.execute(
            text(
                """
                insert into agent_runs (
                    id, session_id, graph_name, graph_version, agent_profile_key,
                    agent_profile_version, thread_id, idempotency_key, status
                )
                values (
                    :id, :session_id, 'walking_skeleton', 'phase0',
                    :agent_profile_key, :agent_profile_version, :thread_id, :idempotency_key, 'created'
                )
                """
            ),
            {
                "id": run_id,
                "session_id": session_id,
                "thread_id": session_id,
                "agent_profile_key": agent_profile_key or "default_research",
                "agent_profile_version": agent_profile_version,
                "idempotency_key": idempotency_key,
            },
        )
        await self.session.commit()
        return {
            "id": run_id,
            "session_id": session_id,
            "status": "created",
            "agent_profile_key": agent_profile_key or "default_research",
            "agent_profile_version": agent_profile_version,
        }

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        result = await self.session.execute(
            text(
                """
                select id, session_id, thread_id, status, resume_token
                from agent_runs
                where id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def set_run_status(
        self,
        *,
        run_id: str,
        session_id: str,
        status: str,
        resume_token: str | None = None,
        error: str | None = None,
    ) -> None:
        current = await self.session.execute(
            text(
                """
                select status
                from agent_runs
                where id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        current_status = current.scalar_one_or_none()
        if current_status is None:
            raise ValueError(f"run not found: {run_id}")
        validate_run_status_transition(str(current_status), status)
        await self.session.execute(
            text(
                """
                update agent_runs
                set status = :status,
                    resume_token = coalesce(:resume_token, resume_token),
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

    async def append_event(self, event: DomainEvent | UIEvent, category: str) -> str:
        event_id = str(uuid.uuid4())
        lineage = event.lineage.model_dump()
        result = await self.session.execute(
            text(
                """
                select coalesce(max(sequence_no), 0) + 1
                from session_events
                where session_id = :session_id
                """
            ),
            {"session_id": event.lineage.session_id},
        )
        sequence_no = int(result.scalar_one())
        await self.session.execute(
            text(
                """
                insert into session_events (
                    id, session_id, run_id, sequence_no, category, event_type,
                    payload_schema_version, lineage, payload, metadata
                )
                values (
                    :id, :session_id, :run_id, :sequence_no, :category, :event_type,
                    :payload_schema_version, cast(:lineage as jsonb),
                    cast(:payload as jsonb), cast(:metadata as jsonb)
                )
                """
            ),
            {
                "id": event_id,
                "session_id": event.lineage.session_id,
                "run_id": event.lineage.run_id,
                "sequence_no": sequence_no,
                "category": category,
                "event_type": event.type,
                "payload_schema_version": event.schema_version,
                "lineage": json.dumps(lineage),
                "payload": json.dumps(event.payload),
                "metadata": "{}",
            },
        )
        await self.session.commit()
        return event_id

    async def list_ui_events(
        self, *, session_id: str, after_event_id: str | None = None
    ) -> list[dict[str, Any]]:
        after_sequence = 0
        if after_event_id:
            result = await self.session.execute(
                text(
                    """
                    select sequence_no from session_events
                    where id = :event_id and session_id = :session_id
                    """
                ),
                {"event_id": after_event_id, "session_id": session_id},
            )
            after_sequence = int(result.scalar() or 0)

        result = await self.session.execute(
            text(
                """
                select id, event_type as type, payload, lineage, payload_schema_version as schema_version
                from session_events
                where session_id = :session_id
                  and category = 'ui'
                  and sequence_no > :after_sequence
                order by sequence_no asc
                """
            ),
            {"session_id": session_id, "after_sequence": after_sequence},
        )
        events = []
        for row in result.mappings().all():
            event = dict(row)
            event["id"] = str(event["id"])
            events.append(event)
        return events

    async def record_side_effect_once(
        self,
        *,
        tool_call_id: str,
        run_id: str,
        result: dict[str, Any] | None = None,
    ) -> bool:
        payload = result or {"message": "phase0 side effect"}
        execute_result = await self.session.execute(
            text(
                """
                insert into tool_side_effects (tool_call_id, run_id, status, result)
                values (:tool_call_id, :run_id, 'completed', cast(:result as jsonb))
                on conflict (tool_call_id) do nothing
                """
            ),
            {
                "tool_call_id": tool_call_id,
                "run_id": run_id,
                "result": json.dumps(payload),
            },
        )
        await self.session.commit()
        return cast(CursorResult[Any], execute_result).rowcount == 1
