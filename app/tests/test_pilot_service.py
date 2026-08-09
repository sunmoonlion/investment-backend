from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

import app.application.agent.pilot_service as pilot_service_module
from app.application.agent.pilot_service import PilotService
from app.application.dto.pilot_runtime import (
    DelegatedUser,
    PilotResumeCommand,
)


class FakeProducer:
    def __init__(self, *, enabled: bool, error: Exception | None = None) -> None:
        self.enabled = enabled
        self.error = error
        self.calls: list[tuple[str, str | None]] = []

    def dispatch_pilot_graph(
        self, run_id: str, resume: str | None = None
    ) -> None:
        self.calls.append((run_id, resume))
        if self.error is not None:
            raise self.error


class FakeRepository:
    def __init__(self, *, run_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        self.run_id = run_id
        self.actor_id = actor_id
        self.session_id = uuid.uuid4()
        self.status = "waiting"
        self.events: list[dict[str, Any]] = []
        self.consumed = True
        self.status_updates: list[dict[str, Any]] = []

    async def consume_resume(self, **_: Any) -> tuple[dict[str, Any], bool]:
        return (
            {
                "id": self.run_id,
                "session_id": self.session_id,
                "status": self.status,
            },
            self.consumed,
        )

    async def set_status(self, **values: Any) -> None:
        self.status = str(values["status"])
        self.status_updates.append(values)

    async def append_browser_event(self, **values: Any) -> dict[str, Any]:
        event = {
            "event_id": uuid.uuid4(),
            "sequence_no": len(self.events) + 1,
            "type": values["event_type"],
            "data": values["data"],
        }
        self.events.append(event)
        return event

    async def get_run(
        self, *, run_id: uuid.UUID, owner_actor_id: uuid.UUID
    ) -> dict[str, Any] | None:
        if run_id != self.run_id or owner_actor_id != self.actor_id:
            return None
        return {
            "id": self.run_id,
            "session_id": self.session_id,
            "status": self.status,
            "title": "Pilot",
            "updated_at": datetime.now(UTC),
        }

    async def list_browser_events(self, **_: Any) -> list[dict[str, Any]]:
        return self.events


def resume_command(actor_id: uuid.UUID) -> PilotResumeCommand:
    return PilotResumeCommand(
        kind="resume",
        idempotency_key=uuid.uuid4(),
        operation_id="op-resume",
        delegated_user=DelegatedUser(
            actor_id=actor_id,
            authenticated_at=datetime.now(UTC),
            policy_version="test-v1",
        ),
        action_id=uuid.uuid4(),
        value="confirm",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("producer", "expected_error"),
    [
        (FakeProducer(enabled=False), "transport is unavailable"),
        (
            FakeProducer(enabled=True, error=RuntimeError("broker rejected")),
            "broker rejected",
        ),
    ],
)
async def test_consumed_resume_dispatch_failure_becomes_terminal(
    monkeypatch: pytest.MonkeyPatch,
    producer: FakeProducer,
    expected_error: str,
) -> None:
    run_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    repository = FakeRepository(run_id=run_id, actor_id=actor_id)
    monkeypatch.setattr(
        pilot_service_module, "get_celery_producer", lambda: producer
    )

    with pytest.raises(RuntimeError, match=expected_error):
        await PilotService(repository).resume(
            run_id=run_id, command=resume_command(actor_id)
        )

    assert repository.status == "failed"
    assert repository.status_updates[-1]["error"] == "resume_dispatch_failed"
    assert repository.events[-1]["type"] == "failed"
    assert (
        repository.events[-1]["data"]["code"] == "resume_dispatch_failed"
    )


@pytest.mark.asyncio
async def test_idempotent_resume_replay_does_not_dispatch_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    repository = FakeRepository(run_id=run_id, actor_id=actor_id)
    repository.consumed = False
    producer = FakeProducer(enabled=True)
    monkeypatch.setattr(
        pilot_service_module, "get_celery_producer", lambda: producer
    )

    snapshot = await PilotService(repository).resume(
        run_id=run_id, command=resume_command(actor_id)
    )

    assert snapshot.status == "waiting_for_input"
    assert producer.calls == []
