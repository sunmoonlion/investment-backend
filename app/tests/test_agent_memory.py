from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.application.agent.memory_service import AgentMemoryService
from app.domain.agent.memory import (
    AgentMemory,
    MemoryKind,
    MemoryScope,
    MemorySourceRef,
    WindowMemoryPolicy,
)
from app.infrastructure.agent.memory_repository import InMemoryAgentMemoryRepository


def make_memory(
    memory_id: str,
    *,
    content: str,
    created_at: datetime,
    sensitive: bool = False,
    scope: MemoryScope = MemoryScope.session,
) -> AgentMemory:
    return AgentMemory(
        id=memory_id,
        session_id="session-1",
        kind=MemoryKind.fact,
        content=content,
        source=MemorySourceRef(source_type="event", source_id=f"event-{memory_id}"),
        confidence=0.8,
        sensitive=sensitive,
        scope=scope,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_memory_policy_windows_session_memories_and_summarizes() -> None:
    repository = InMemoryAgentMemoryRepository()
    policy = WindowMemoryPolicy(max_memories=2)
    service = AgentMemoryService(repository, policy)
    now = datetime.now(UTC)

    await service.append(make_memory("1", content="old", created_at=now - timedelta(minutes=3)))
    await service.append(make_memory("2", content="middle", created_at=now - timedelta(minutes=2)))
    await service.append(make_memory("3", content="new", created_at=now - timedelta(minutes=1)))

    window = await service.build_window("session-1")

    assert [memory.content for memory in window.memories] == ["middle", "new"]
    assert window.summary == "- fact: middle\n- fact: new"


@pytest.mark.asyncio
async def test_memory_layer_requires_source_confidence_scope_and_safety_flags() -> None:
    memory = make_memory(
        "1",
        content="remembered fact",
        created_at=datetime.now(UTC),
        sensitive=True,
    )
    repository = InMemoryAgentMemoryRepository()
    service = AgentMemoryService(repository, WindowMemoryPolicy(max_memories=10))

    await service.append(memory)
    window = await service.build_window("session-1")

    assert window.memories == []
    assert memory.source.source_type == "event"
    assert memory.source.source_id == "event-1"
    assert memory.confidence == 0.8
    assert memory.scope == MemoryScope.session
    assert memory.sensitive is True
    assert memory.schema_version == 1


@pytest.mark.asyncio
async def test_memory_policy_does_not_mix_long_term_memory_into_session_window() -> None:
    repository = InMemoryAgentMemoryRepository()
    service = AgentMemoryService(repository, WindowMemoryPolicy(max_memories=10))
    now = datetime.now(UTC)

    await service.append(
        make_memory(
            "long-term",
            content="cross-session fact",
            created_at=now,
            scope=MemoryScope.long_term,
        )
    )
    await service.append(make_memory("session", content="session fact", created_at=now))

    window = await service.build_window("session-1")

    assert [memory.content for memory in window.memories] == ["session fact"]
