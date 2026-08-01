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
        self.redis = redis
        self.projector = TimelineProjector()

    async def append(self, event: DomainEvent) -> str:
        await self.repository.append_event(event, "domain")
        ui_event = self.projector.project(event)
        ui_event.id = await self.repository.append_event(ui_event, "ui")
        if self.redis:
            await self.redis.publish(
                session_events_channel(event.lineage.session_id),
                ui_event.model_dump_json(),
            )
            live_delta = LiveDelta(
                payload={
                    "ui_event_type": ui_event.type,
                    "domain_event_type": event.type,
                },
                final_event_id=ui_event.id,
                lineage=event.lineage,
                schema_version=event.schema_version,
            )
            await self.redis.publish(
                session_deltas_channel(event.lineage.session_id),
                live_delta.model_dump_json(),
            )
        return ui_event.id
