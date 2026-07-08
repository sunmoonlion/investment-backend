from __future__ import annotations

import pytest

from app.domain.agent.llm import EvidenceRef, LLMRequest, build_llm_context
from app.domain.agent.memory import AgentMemory, MemoryKind, MemorySourceRef
from app.domain.agent.models import MessageRole, StoredMessage
from app.infrastructure.agent.fake_llm import DeterministicFakeLLM


def test_llm_context_is_temporary_package_with_refs_only() -> None:
    message = StoredMessage(role=MessageRole.user, content="hello", sequence_no=1)
    memory = AgentMemory(
        id="memory-1",
        session_id="session-1",
        kind=MemoryKind.fact,
        content="remembered fact",
        source=MemorySourceRef(source_type="event", source_id="event-1"),
        confidence=0.9,
    )
    evidence = EvidenceRef(source_type="artifact", source_id="artifact-1", source_uri="s3://b/k")

    context = build_llm_context(
        prompt_id="prompt.test.v1",
        messages=[message],
        memories=[memory],
        evidence_refs=[evidence],
    )

    assert context.messages == [message]
    assert context.memory_refs == ["memory-1"]
    assert context.evidence_refs == [evidence]
    assert "remembered fact" not in context.model_dump_json()


@pytest.mark.asyncio
async def test_deterministic_fake_llm_uses_adapter_boundary_without_live_call() -> None:
    context = build_llm_context(
        prompt_id="prompt.test.v1",
        messages=[StoredMessage(role=MessageRole.user, content="hello", sequence_no=1)],
    )

    response = await DeterministicFakeLLM().complete(
        LLMRequest(model_key="fake", context=context)
    )

    assert response.message.role == MessageRole.assistant
    assert response.message.content == "fake:prompt.test.v1:hello"
    assert response.usage == {
        "input_messages": 1,
        "memory_refs": 0,
        "evidence_refs": 0,
    }
