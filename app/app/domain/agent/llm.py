from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from app.domain.agent.memory import AgentMemory
from app.domain.agent.models import MessageRole, StoredMessage


class EvidenceRef(BaseModel):
    source_type: str
    source_id: str
    source_uri: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class LLMContext(BaseModel):
    prompt_id: str
    messages: list[StoredMessage]
    memory_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class LLMRequest(BaseModel):
    model_key: str
    context: LLMContext
    temperature: float = 0.0


class LLMResponse(BaseModel):
    message: StoredMessage
    model_key: str
    prompt_id: str
    usage: dict[str, int] = Field(default_factory=dict)


class LLMPort(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        ...


def build_llm_context(
    *,
    prompt_id: str,
    messages: list[StoredMessage],
    memories: list[AgentMemory] | None = None,
    evidence_refs: list[EvidenceRef] | None = None,
) -> LLMContext:
    return LLMContext(
        prompt_id=prompt_id,
        messages=messages,
        memory_refs=[memory.id for memory in memories or []],
        evidence_refs=evidence_refs or [],
    )


def make_assistant_message(content: str, *, sequence_no: int) -> StoredMessage:
    return StoredMessage(
        role=MessageRole.assistant,
        content=content,
        sequence_no=sequence_no,
    )
