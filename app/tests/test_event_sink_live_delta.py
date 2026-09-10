from __future__ import annotations

import json
from typing import Any

import pytest
from agent_test_fakes import FakeTransactions

from app.application.agent.event_sink import DBEventSink
from app.domain.agent.models import DomainEvent, RunLineage, UIEvent


class FakeEventRepository(FakeTransactions):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[str, DomainEvent | UIEvent]] = []

    async def append_event(self, event: DomainEvent | UIEvent, category: str) -> str:
        self.events.append((category, event))
        return f"{category}-{len(self.events)}"


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1


@pytest.mark.asyncio
async def test_event_sink_publishes_ui_event_and_reconciled_live_delta() -> None:
    repository = FakeEventRepository()
    redis = FakeRedis()
    lineage = RunLineage(session_id="session-1", run_id="run-1", root_run_id="run-1")
    event = DomainEvent(type="RunStarted", payload={"ok": True}, lineage=lineage)

    event_id = await DBEventSink(repository, redis).append(event)  # type: ignore[arg-type]

    assert event_id == "ui-2"
    assert [category for category, _ in repository.events] == ["domain", "ui"]
    assert redis.published == []
    published = [
        (item["channel"], json.dumps(item["payload"])) for item in repository.outbox
    ]
    assert [channel for channel, _ in published] == [
        "investment:agent:session:session-1:events",
        "investment:agent:session:session-1:deltas",
    ]

    ui_payload = json.loads(published[0][1])
    delta_payload: dict[str, Any] = json.loads(published[1][1])

    assert ui_payload["id"] == "ui-2"
    assert ui_payload["type"] == "TimelineRunStarted"
    assert delta_payload["type"] == "LiveDelta"
    assert delta_payload["final_event_id"] == "ui-2"
    assert delta_payload["payload"] == {
        "ui_event_type": "TimelineRunStarted",
        "domain_event_type": "RunStarted",
    }
