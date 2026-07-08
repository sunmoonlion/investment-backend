from __future__ import annotations

from redis.asyncio import Redis

from app.application.agent.timeline_projector import TimelineProjector
from app.domain.agent.models import DomainEvent
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
                f"agent:session:{event.lineage.session_id}:events",
                ui_event.model_dump_json(),
            )
        return ui_event.id

