from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

import app.interfaces.endpoints.agent_routes as agent_routes
from app.domain.agent.models import DomainEvent, RunLineage
from app.infrastructure.agent.repositories import AgentRepository


class FakePubSub:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = messages
        self.subscribed: tuple[str, ...] | None = None
        self.unsubscribed: tuple[str, ...] | None = None
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        self.subscribed = channels

    async def unsubscribe(self, *channels: str) -> None:
        self.unsubscribed = channels

    async def aclose(self) -> None:
        self.closed = True

    async def listen(self) -> AsyncIterator[dict]:
        for message in self.messages:
            yield message


class FakeRedis:
    def __init__(self, pubsub: FakePubSub) -> None:
        self.pubsub_instance = pubsub
        self.client = self

    def pubsub(self) -> FakePubSub:
        return self.pubsub_instance


class FakeSessionFactory:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_: object) -> None:
        return None


class FakePostgres:
    def __init__(self) -> None:
        self.session_factory_instance = FakeSessionFactory()

    def session_factory(self) -> FakeSessionFactory:
        return self.session_factory_instance


class FakeRepository:
    def __init__(self, _session: object) -> None:
        pass

    async def assert_session_owner(self, **_: str) -> None:
        return None

    async def list_ui_events(self, **_: str | None) -> list[dict]:
        return [
            {
                "id": "replayed-event",
                "type": "TimelineRunStarted",
                "payload": {"source": "database"},
            }
        ]


@pytest.mark.asyncio
async def test_stream_subscribes_before_snapshot_and_deduplicates_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "session-stream"
    event_channel = agent_routes.session_events_channel(session_id)
    delta_channel = agent_routes.session_deltas_channel(session_id)
    pubsub = FakePubSub(
        [
            {
                "type": "message",
                "channel": event_channel,
                "data": json.dumps(
                    {
                        "id": "replayed-event",
                        "type": "TimelineRunStarted",
                        "payload": {"source": "redis-overlap"},
                    }
                ),
            },
            {
                "type": "message",
                "channel": event_channel,
                "data": json.dumps(
                    {
                        "id": "new-event",
                        "type": "TimelineRunCompleted",
                        "payload": {"source": "redis-live"},
                    }
                ),
            },
            {
                "type": "message",
                "channel": delta_channel,
                "data": json.dumps(
                    {
                        "type": "LiveDelta",
                        "final_event_id": "replayed-event",
                        "payload": {"source": "redis-live"},
                    }
                ),
            },
        ]
    )
    monkeypatch.setattr(agent_routes, "get_redis", lambda: FakeRedis(pubsub))
    monkeypatch.setattr(agent_routes, "get_postgres", lambda: FakePostgres())
    monkeypatch.setattr(agent_routes, "AgentRepository", FakeRepository)

    response = await agent_routes.stream_events(
        session_id,
        principal=SimpleNamespace(actor_id="actor-1"),  # type: ignore[arg-type]
        session=object(),  # type: ignore[arg-type]
    )
    raw_chunks = [chunk async for chunk in response.body_iterator]
    chunks = [chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in raw_chunks]

    assert pubsub.subscribed == (event_channel, delta_channel)
    assert pubsub.unsubscribed == (event_channel, delta_channel)
    assert pubsub.closed is True
    assert len(chunks) == 3
    assert '"source": "database"' in chunks[0]
    assert '"id": "replayed-event"' not in chunks[1]
    assert '"id": "new-event"' in chunks[1]
    assert '"final_event_id": "replayed-event"' in chunks[2]


class FakeScalarResult:
    def scalar_one(self) -> int:
        return 1


class RecordingSession:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict | None]] = []
        self.committed = False

    async def execute(self, statement: object, parameters: dict | None = None) -> FakeScalarResult:
        self.statements.append((str(statement), parameters))
        return FakeScalarResult()

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.asyncio
async def test_append_event_locks_session_before_allocating_sequence() -> None:
    session = RecordingSession()
    repository = AgentRepository(session)  # type: ignore[arg-type]
    event = DomainEvent(
        type="RunStarted",
        lineage=RunLineage(session_id="session-1", run_id="run-1"),
    )

    await repository.append_event(event, "domain")

    statements = [sql.lower() for sql, _ in session.statements]
    assert "pg_advisory_xact_lock" in statements[0]
    assert "max(sequence_no)" in statements[1]
    assert "insert into session_events" in statements[2]
    assert session.statements[0][1] == {"session_id": "session-1"}
    assert session.committed is True
