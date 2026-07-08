from __future__ import annotations

from app.domain.agent.sandbox import SandboxAction, SandboxRequest, SandboxResult


class DeterministicFakeSandbox:
    async def run(self, request: SandboxRequest) -> SandboxResult:
        if request.action == SandboxAction.shell:
            return SandboxResult(
                action=request.action,
                exit_code=0,
                stdout=f"fake-shell:{request.command or ''}",
                metadata={"adapter": "deterministic_fake"},
            )
        if request.action == SandboxAction.python:
            return SandboxResult(
                action=request.action,
                exit_code=0,
                stdout=f"fake-python:{request.code or ''}",
                metadata={"adapter": "deterministic_fake"},
            )
        if request.action == SandboxAction.file_read:
            return SandboxResult(
                action=request.action,
                exit_code=0,
                stdout=f"fake-read:{request.path or ''}",
                metadata={"adapter": "deterministic_fake"},
            )
        if request.action == SandboxAction.file_write:
            artifact_id = f"fake-artifact:{request.path or 'unnamed'}"
            return SandboxResult(
                action=request.action,
                exit_code=0,
                artifact_ids=[artifact_id],
                metadata={"adapter": "deterministic_fake"},
            )
        return SandboxResult(
            action=request.action,
            exit_code=2,
            stderr=f"unsupported sandbox action: {request.action}",
            metadata={"adapter": "deterministic_fake"},
        )
