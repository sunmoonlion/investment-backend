from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field


class MemoryScope(StrEnum):
    session = "session"
    long_term = "long_term"


class MemoryKind(StrEnum):
    fact = "fact"
    preference = "preference"
    summary = "summary"
    experience = "experience"


class MemorySourceRef(BaseModel):
    source_type: str
    source_id: str
    source_uri: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class AgentMemory(BaseModel):
    id: str
    session_id: str
    scope: MemoryScope = MemoryScope.session
    kind: MemoryKind
    content: str
    source: MemorySourceRef
    confidence: float = Field(ge=0.0, le=1.0)
    sensitive: bool = False
    ttl_seconds: int | None = None
    schema_version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryWindow(BaseModel):
    memories: list[AgentMemory]
    summary: str | None = None


class MemoryPolicy(Protocol):
    key: str

    def select_window(self, memories: list[AgentMemory]) -> list[AgentMemory]:
        ...

    def summarize(self, memories: list[AgentMemory]) -> str | None:
        ...


class WindowMemoryPolicy:
    def __init__(
        self,
        *,
        key: str = "session_window_default",
        max_memories: int = 20,
        include_sensitive: bool = False,
    ):
        self.key = key
        self.max_memories = max_memories
        self.include_sensitive = include_sensitive

    def select_window(self, memories: list[AgentMemory]) -> list[AgentMemory]:
        eligible = [
            memory
            for memory in memories
            if memory.scope == MemoryScope.session
            and (self.include_sensitive or not memory.sensitive)
        ]
        return sorted(eligible, key=lambda memory: memory.created_at)[-self.max_memories :]

    def summarize(self, memories: list[AgentMemory]) -> str | None:
        if not memories:
            return None
        lines = [f"- {memory.kind.value}: {memory.content}" for memory in memories]
        return "\n".join(lines)
