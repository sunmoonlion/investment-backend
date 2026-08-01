from __future__ import annotations

import pytest

from app.domain.agent.profiles import EffectiveAgentConfig, MemoryPolicyConfig
from app.domain.agent.sandbox import SandboxAction, SandboxRequest
from app.infrastructure.agent.fake_sandbox import DeterministicFakeSandbox


def make_effective_config(*, allowed_tools: set[str], denied_tools: set[str] | None = None):
    return EffectiveAgentConfig(
        profile_key="test",
        profile_version=1,
        system_prompt_id="prompt.test.v1",
        model_key="fake",
        allowed_tools=allowed_tools,
        denied_tools=denied_tools or set(),
        memory_policy=MemoryPolicyConfig(key="test"),
    )


@pytest.mark.asyncio
async def test_fake_sandbox_is_deterministic_and_does_not_execute_real_shell() -> None:
    result = await DeterministicFakeSandbox().run(
        SandboxRequest(action=SandboxAction.shell, command="rm -rf /")
    )

    assert result.succeeded
    assert result.stdout == "fake-shell:rm -rf /"
    assert result.metadata["adapter"] == "deterministic_fake"


@pytest.mark.asyncio
async def test_fake_sandbox_file_write_returns_artifact_ref_id() -> None:
    result = await DeterministicFakeSandbox().run(
        SandboxRequest(
            action=SandboxAction.file_write,
            path="/workspace/report.md",
            content="# Report",
        )
    )

    assert result.succeeded
    assert result.artifact_ids == ["fake-artifact:/workspace/report.md"]
    assert result.stdout == ""


def test_agent_profile_controls_sandbox_tool_permission() -> None:
    config = make_effective_config(allowed_tools={"file_read"}, denied_tools={"shell"})

    assert config.permits_tool("file_read")
    assert not config.permits_tool("shell")
    assert not config.permits_tool("file_write")
