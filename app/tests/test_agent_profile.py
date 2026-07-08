from __future__ import annotations

import pytest
from pytest import MonkeyPatch

from app.application.agent.profile_catalog import build_builtin_profile_catalog
from app.application.agent.run_service import AgentRunService
from app.domain.agent.commands import CreateRunCommand, ResumeRunCommand
from app.domain.agent.models import UserInput


class DisabledProducer:
    enabled = False


class FakeRepository:
    def __init__(self) -> None:
        self.create_run_kwargs: dict | None = None
        self.run: dict | None = None

    async def create_session(self) -> str:
        return "session-1"

    async def create_run(self, **kwargs):
        self.create_run_kwargs = kwargs
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


def test_builtin_agent_profiles_resolve_different_effective_configs() -> None:
    catalog = build_builtin_profile_catalog()

    default = catalog.resolve("default_research")
    literature = catalog.resolve("literature_review")

    assert default.profile_key == "default_research"
    assert literature.profile_key == "literature_review"
    assert default.system_prompt_id != literature.system_prompt_id
    assert default.memory_policy.key != literature.memory_policy.key
    assert default.permits_tool("file_write")
    assert not literature.permits_tool("file_write")
    assert not default.permits_tool("shell")


def test_unknown_agent_profile_is_rejected() -> None:
    catalog = build_builtin_profile_catalog()

    with pytest.raises(ValueError, match="unknown agent_profile_key"):
        catalog.resolve("missing")


@pytest.mark.asyncio
async def test_run_service_persists_resolved_profile_identity() -> None:
    repository = FakeRepository()
    service = AgentRunService(repository, profile_catalog=build_builtin_profile_catalog())

    result = await service.create_run(
        CreateRunCommand(
            session_id="session-1",
            user_input=UserInput(text="start"),
            agent_profile_key="literature_review",
        )
    )

    assert repository.create_run_kwargs is not None
    assert repository.create_run_kwargs["agent_profile_key"] == "literature_review"
    assert repository.create_run_kwargs["agent_profile_version"] == 1
    assert result["agent_profile_key"] == "literature_review"
    assert result["agent_profile_version"] == 1


@pytest.mark.asyncio
async def test_resume_run_requires_waiting_status(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.application.agent.run_service.get_celery_producer",
        lambda: DisabledProducer(),
    )
    repository = FakeRepository()
    repository.run = {
        "id": "run-1",
        "session_id": "session-1",
        "status": "running",
        "resume_token": "phase0:run-1",
    }
    service = AgentRunService(repository, profile_catalog=build_builtin_profile_catalog())

    with pytest.raises(ValueError, match="run is not waiting for input"):
        await service.resume_run(
            ResumeRunCommand(
                run_id="run-1",
                resume_token="phase0:run-1",
                user_input=UserInput(text="continue"),
            )
        )


@pytest.mark.asyncio
async def test_resume_run_requires_stored_resume_token(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.application.agent.run_service.get_celery_producer",
        lambda: DisabledProducer(),
    )
    repository = FakeRepository()
    repository.run = {
        "id": "run-1",
        "session_id": "session-1",
        "status": "waiting",
        "resume_token": None,
    }
    service = AgentRunService(repository, profile_catalog=build_builtin_profile_catalog())

    with pytest.raises(ValueError, match="run has no resume_token"):
        await service.resume_run(
            ResumeRunCommand(
                run_id="run-1",
                resume_token="phase0:run-1",
                user_input=UserInput(text="continue"),
            )
        )


@pytest.mark.asyncio
async def test_resume_run_accepts_valid_waiting_token(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.application.agent.run_service.get_celery_producer",
        lambda: DisabledProducer(),
    )
    repository = FakeRepository()
    repository.run = {
        "id": "run-1",
        "session_id": "session-1",
        "status": "waiting",
        "resume_token": "phase0:run-1",
    }
    service = AgentRunService(repository, profile_catalog=build_builtin_profile_catalog())

    result = await service.resume_run(
        ResumeRunCommand(
            run_id="run-1",
            resume_token="phase0:run-1",
            user_input=UserInput(text="continue"),
        )
    )

    assert result == {"run_id": "run-1", "session_id": "session-1", "enqueued": False}
