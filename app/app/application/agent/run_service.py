from __future__ import annotations

from sqlalchemy import text

from app.application.agent.event_sink import DBEventSink
from app.application.agent.profile_catalog import (
    AgentProfileCatalog,
    builtin_profile_catalog,
)
from app.domain.agent.commands import CreateRunCommand, ResumeRunCommand
from app.domain.agent.models import DomainEvent, RunLineage, RunStatus
from app.infrastructure.agent.repositories import AgentRepository


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
        async with self.repository.transaction():
            run = await self.repository.create_run(
                session_id=command.session_id,
                owner_actor_id=command.owner_actor_id,
                idempotency_key=command.idempotency_key,
                agent_profile_key=effective_config.profile_key,
                agent_profile_version=effective_config.profile_version,
            )
            run_id = str(run["id"])
            if run["created"]:
                await DBEventSink(self.repository).append(
                    DomainEvent(
                        type="RunQueued",
                        lineage=RunLineage(
                            session_id=str(run["session_id"]), run_id=run_id
                        ),
                    )
                )
                await self.repository.enqueue(
                    topic="agent.execution",
                    key=f"start:{run_id}",
                    run_id=run_id,
                    payload={
                        "run_id": run_id,
                        "kind": "phase0",
                        "operation": "start",
                        "input": command.user_input.text,
                        "security_context": command.security_context.model_dump(
                            mode="json"
                        ),
                    },
                )
            run["enqueued"] = True
        return {
            "run_id": run_id,
            "session_id": run["session_id"],
            "status": run["status"],
            "enqueued": run["enqueued"],
            "agent_profile_key": effective_config.profile_key,
            "agent_profile_version": effective_config.profile_version,
        }

    async def resume_run(self, command: ResumeRunCommand) -> dict:
        async with self.repository.transaction():
            await self.repository.session.execute(
                text("select id from agent_runs where id = :id for update"),
                {"id": command.run_id},
            )
            run = await self.repository.get_run(command.run_id)
            if not run:
                raise ValueError("run not found")
            if (
                command.owner_actor_id is not None
                and str(run.get("owner_actor_id")) != command.owner_actor_id
            ):
                raise PermissionError("run belongs to another actor")
            prior = await self.repository.get_command(
                f"resume:{command.run_id}:{command.resume_token}"
            )
            if prior is not None:
                if prior.get("input") != command.user_input.text or prior.get(
                    "security_context"
                ) != command.security_context.model_dump(mode="json"):
                    raise ValueError("resume token reused with different input")
                return {
                    "run_id": command.run_id,
                    "session_id": run["session_id"],
                    "enqueued": True,
                }
            if (
                run["status"] != RunStatus.waiting
                or run["resume_token"] != command.resume_token
            ):
                raise ValueError("invalid or consumed resume_token")
            await self.repository.session.execute(
                text("update agent_runs set resume_token = null where id = :id"),
                {"id": command.run_id},
            )
            await DBEventSink(self.repository).append(
                DomainEvent(
                    type="UserInputReceived",
                    payload={"text": command.user_input.text},
                    lineage=RunLineage(
                        session_id=str(run["session_id"]), run_id=command.run_id
                    ),
                )
            )
            await self.repository.enqueue(
                topic="agent.execution",
                key=f"resume:{command.run_id}:{command.resume_token}",
                run_id=command.run_id,
                payload={
                    "run_id": command.run_id,
                    "kind": "phase0",
                    "operation": "resume",
                    "input": command.user_input.text,
                    "security_context": command.security_context.model_dump(
                        mode="json"
                    ),
                },
            )
        return {
            "run_id": command.run_id,
            "session_id": run["session_id"],
            "enqueued": True,
        }
