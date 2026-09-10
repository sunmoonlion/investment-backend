from __future__ import annotations

from redis.asyncio import Redis

from app.application.agent.redis_keys import (
    session_deltas_channel,
    session_events_channel,
)
from app.application.agent.timeline_projector import TimelineProjector
from app.domain.agent.models import DomainEvent, LiveDelta
from app.infrastructure.agent.repositories import AgentRepository


class DBEventSink:
    def __init__(self, repository: AgentRepository, redis: Redis | None = None):
        self.repository = repository
        self.projector = TimelineProjector()

    async def append(self, event: DomainEvent) -> str:
        # No network operation inside the business transaction. The dispatcher
        # retries notifications using these same event IDs; DB replay is primary.
        async with self.repository.transaction():
            await self.repository.append_event(event, "domain")
            ui_event = self.projector.project(event)
            ui_event.id = await self.repository.append_event(ui_event, "ui")
            await self.repository.notify(
                key=f"ui:{ui_event.id}",
                run_id=event.lineage.run_id,
                channel=session_events_channel(event.lineage.session_id),
                payload=ui_event.model_dump(mode="json"),
            )
            delta = LiveDelta(
                payload={
                    "ui_event_type": ui_event.type,
                    "domain_event_type": event.type,
                },
                final_event_id=ui_event.id,
                lineage=event.lineage,
                schema_version=event.schema_version,
            )
            await self.repository.notify(
                key=f"delta:{ui_event.id}",
                run_id=event.lineage.run_id,
                channel=session_deltas_channel(event.lineage.session_id),
                payload=delta.model_dump(mode="json"),
            )
        return ui_event.id
