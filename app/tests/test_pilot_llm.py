from __future__ import annotations

import uuid

import httpx
import pytest

from app.domain.agent.knowledge import EvidenceProviderMetadata, KnowledgeEvidence
from app.infrastructure.external.pilot_llm import (
    OpenAICompatiblePilotLLM,
    PilotLLMError,
)


def evidence() -> KnowledgeEvidence:
    return KnowledgeEvidence(
        evidence_id=uuid.uuid4(),
        knowledge_document_id=uuid.uuid4(),
        knowledge_document_version_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        content="Grounded source content.",
        score=0.9,
        rank=1,
        title="Source",
        source_uri=None,
        source_app="info-app",
        source_document_id=uuid.uuid4(),
        source_document_version_id=uuid.uuid4(),
        content_hash="b" * 64,
        token_estimate=10,
        truncated=False,
        access_scope=["research"],
        provider_metadata=EvidenceProviderMetadata(provider="ragflow"),
    )


@pytest.mark.asyncio
async def test_pilot_llm_uses_openai_compatible_contract_without_leaking_key() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/compatible-mode/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer secret-value"
        assert b"Grounded source content." in await request.aread()
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Grounded answer."}}]},
        )

    client = OpenAICompatiblePilotLLM(
        base_url="https://model.invalid/compatible-mode/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(handler),
    )
    assert (
        await client.answer(user_input="Question", evidence=[evidence()])
        == "Grounded answer."
    )


@pytest.mark.asyncio
async def test_pilot_llm_fails_closed_on_invalid_response() -> None:
    client = OpenAICompatiblePilotLLM(
        base_url="https://model.invalid/v1",
        api_key="secret-value",
        model="qwen-plus",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )
    with pytest.raises(PilotLLMError, match="violates"):
        await client.answer(user_input="Question", evidence=[evidence()])
