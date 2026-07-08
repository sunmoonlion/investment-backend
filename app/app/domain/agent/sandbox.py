from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class SandboxAction(StrEnum):
    shell = "shell"
    python = "python"
    file_read = "file_read"
    file_write = "file_write"


class SandboxRequest(BaseModel):
    action: SandboxAction
    command: str | None = None
    code: str | None = None
    path: str | None = None
    content: str | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SandboxResult(BaseModel):
    action: SandboxAction
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


class SandboxPort(Protocol):
    async def run(self, request: SandboxRequest) -> SandboxResult:
        ...
