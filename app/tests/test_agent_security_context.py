from __future__ import annotations

import pytest
from pytest import MonkeyPatch

from app.application.agent.profile_catalog import build_builtin_profile_catalog
from app.application.agent.run_service import AgentRunService
from app.domain.agent.commands import CreateRunCommand, ResumeRunCommand
from app.domain.agent.models import UserInput
from app.domain.agent.security import SecurityContext


class EnabledProducer:
    enabled = True

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def dispatch_agent_graph(
        self,
        run_id: str,
        user_input: str | None = None,
        security_context: dict | None = None,
    ) -> str:
        self.calls.append(
            {
                "run_id": run_id,
                "user_input": user_input,
                "security_context": security_context,
            }
        )
        return "task-1"


class FakeRepository:
    def __init__(self) -> None:
        self.run: dict | None = None

    async def create_run(self, **kwargs):
        return {
            "id": "run-1",
            "session_id": kwargs["session_id"],
            "status": "created",
            "agent_profile_key": kwargs["agent_profile_key"],
            "agent_profile_version": kwargs["agent_profile_version"],
        }

    async def get_run(self, run_id: str):
        if not self.run or self.run["id"] != run_id:
            return None
        return self.run


def test_commands_default_to_single_tenant_security_context() -> None:
    command = CreateRunCommand(session_id="session-1")

    assert command.security_context == SecurityContext.single_tenant()
    assert command.security_context.tenant_id == "single-tenant"
    assert command.security_context.actor_id == "system"


@pytest.mark.asyncio
async def test_create_run_dispatches_security_context(monkeypatch: MonkeyPatch) -> None:
    producer = EnabledProducer()
    monkeypatch.setattr(
        "app.application.agent.run_service.get_celery_producer",
        lambda: producer,
    )
    service = AgentRunService(FakeRepository(), profile_catalog=build_builtin_profile_catalog())

    result = await service.create_run(
        CreateRunCommand(
            session_id="session-1",
            user_input=UserInput(text="start"),
            security_context=SecurityContext(actor_id="user-1"),
        )
    )

    assert result["enqueued"] is True
    assert producer.calls == [
            {
                "run_id": "run-1",
                "user_input": "start",
            "security_context": {
                "tenant_id": "single-tenant",
                "actor_id": "user-1",
                "roles": ["agent_user"],
                "permissions": [],
                "schema_version": 1,
            },
        }
    ]


@pytest.mark.asyncio
async def test_resume_run_dispatches_security_context(monkeypatch: MonkeyPatch) -> None:
    producer = EnabledProducer()
    monkeypatch.setattr(
        "app.application.agent.run_service.get_celery_producer",
        lambda: producer,
    )
    repository = FakeRepository()
    repository.run = {
        "id": "run-1",
        "session_id": "session-1",
        "status": "waiting",
        "resume_token": "phase0:run-1",
    }
    service = AgentRunService(repository, profile_catalog=build_builtin_profile_catalog())

    await service.resume_run(
        ResumeRunCommand(
            run_id="run-1",
            resume_token="phase0:run-1",
            user_input=UserInput(text="continue"),
            security_context=SecurityContext(actor_id="user-2"),
        )
    )

    assert producer.calls == [
        {
            "run_id": "run-1",
            "user_input": "continue",
            "security_context": {
                "tenant_id": "single-tenant",
                "actor_id": "user-2",
                "roles": ["agent_user"],
                "permissions": [],
                "schema_version": 1,
            },
        }
    ]
