from __future__ import annotations

from app.application.agent.profile_catalog import (
    AgentProfileCatalog,
    builtin_profile_catalog,
)
from app.domain.agent.commands import CreateRunCommand, ResumeRunCommand
from app.domain.agent.models import RunStatus
from app.infrastructure.agent.repositories import AgentRepository
from app.infrastructure.messaging.celery_producer import get_celery_producer


class AgentRunService:
    def __init__(
        self,
        repository: AgentRepository,
        profile_catalog: AgentProfileCatalog = builtin_profile_catalog,
    ):
        self.repository = repository
        self.profile_catalog = profile_catalog

    async def create_session(self, *, owner_actor_id: str | None = None) -> str:
        return await self.repository.create_session(owner_actor_id=owner_actor_id)

    async def create_run(self, command: CreateRunCommand) -> dict:
        effective_config = self.profile_catalog.resolve(command.agent_profile_key)
        run = await self.repository.create_run(
            session_id=command.session_id,
            owner_actor_id=command.owner_actor_id,
            idempotency_key=command.idempotency_key,
            agent_profile_key=effective_config.profile_key,
            agent_profile_version=effective_config.profile_version,
        )
        producer = get_celery_producer()
        run_id = run["id"]
        if producer.enabled:
            producer.dispatch_agent_graph(
                run_id,
                command.user_input.text,
                security_context=command.security_context.model_dump(),
            )
            run["enqueued"] = True
        else:
            run["enqueued"] = False
        return {
            "run_id": run_id,
            "session_id": run["session_id"],
            "status": run["status"],
            "enqueued": run["enqueued"],
            "agent_profile_key": effective_config.profile_key,
            "agent_profile_version": effective_config.profile_version,
        }

    async def resume_run(self, command: ResumeRunCommand) -> dict:
        run = await self.repository.get_run(command.run_id)
        if not run:
            raise ValueError("run not found")
        if (
            command.owner_actor_id is not None
            and str(run.get("owner_actor_id")) != command.owner_actor_id
        ):
            raise PermissionError("run belongs to another actor")
        if run.get("status") != RunStatus.waiting:
            raise ValueError("run is not waiting for input")
        if not run.get("resume_token"):
            raise ValueError("run has no resume_token")
        if run["resume_token"] != command.resume_token:
            raise ValueError("invalid resume_token")
        producer = get_celery_producer()
        if producer.enabled:
            producer.dispatch_agent_graph(
                command.run_id,
                command.user_input.text,
                security_context=command.security_context.model_dump(),
            )
            enqueued = True
        else:
            enqueued = False
        return {
            "run_id": command.run_id,
            "session_id": run["session_id"],
            "enqueued": enqueued,
        }
