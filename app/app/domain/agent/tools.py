from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.domain.agent.models import DomainEvent, RunLineage, StoredMessage


class ToolExecutionStatus(StrEnum):
    succeeded = "succeeded"
    failed = "failed"


class ArtifactRef(BaseModel):
    id: str
    uri: str
    media_type: str | None = None
    hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResult(BaseModel):
    tool_name: str
    tool_call_id: str
    status: ToolExecutionStatus
    content: str = ""
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ToolResultProjection(BaseModel):
    llm_message: StoredMessage
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    domain_events: list[DomainEvent] = Field(default_factory=list)


class ToolExecutionPort(Protocol):
    async def execute(
        self, tool_name: str, args: dict[str, Any]
    ) -> ToolExecutionResult: ...


class ToolResultHandler(Protocol):
    def handle(
        self,
        result: ToolExecutionResult,
        *,
        lineage: RunLineage,
        sequence_no: int,
    ) -> ToolResultProjection: ...
