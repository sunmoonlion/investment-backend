"""Session-level execution contract, independent of any graph or model SDK."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_id: str
    input: dict[str, Any] = Field(default_factory=dict)


class ExecutionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    execution_id: str
    provider: str
    state: dict[str, Any] = Field(default_factory=dict)
    status: Literal["running", "waiting", "completed", "cancelled"] = "running"


class ExecutionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int
    type: Literal["snapshot"] = "snapshot"
    binding: ExecutionBinding


class AgentExecutorPort(Protocol):
    async def start(self, request: ExecutionRequest) -> ExecutionBinding: ...
    async def resume(
        self, binding: ExecutionBinding, value: str, *, execution_id: str
    ) -> ExecutionBinding: ...
    def events(
        self, binding: ExecutionBinding, *, after: int = 0
    ) -> AsyncIterator[ExecutionEvent]: ...
    async def inspect(self, binding: ExecutionBinding) -> ExecutionBinding: ...
    async def cancel(self, binding: ExecutionBinding) -> ExecutionBinding: ...
    async def close(self, binding: ExecutionBinding) -> None: ...
