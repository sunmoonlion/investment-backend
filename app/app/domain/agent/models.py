from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SessionStatus(StrEnum):
    created = "created"
    running = "running"
    waiting = "waiting"
    completed = "completed"
    failed = "failed"


class RunStatus(StrEnum):
    created = "created"
    running = "running"
    waiting = "waiting"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class MessageRole(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class UserInput(BaseModel):
    text: str = ""
    attachment_ids: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class StoredMessage(BaseModel):
    role: MessageRole
    content: str
    sequence_no: int = Field(ge=1)
    message_id: str | None = None
    tool_call_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = 1


class RunLineage(BaseModel):
    session_id: str
    run_id: str
    root_run_id: str | None = None
    parent_run_id: str | None = None


class DomainEvent(BaseModel):
    type: str
    payload: dict = Field(default_factory=dict)
    lineage: RunLineage
    schema_version: int = 1


class UIEvent(BaseModel):
    id: str | None = None
    type: str
    payload: dict = Field(default_factory=dict)
    lineage: RunLineage
    schema_version: int = 1
