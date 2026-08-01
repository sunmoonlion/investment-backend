from __future__ import annotations

from app.application.agent.tool_result_handlers import (
    FileToolResultHandler,
    ToolResultHandlerRegistry,
    build_default_tool_result_registry,
)
from app.domain.agent.models import MessageRole, RunLineage
from app.domain.agent.tools import ArtifactRef, ToolExecutionResult, ToolExecutionStatus


def test_default_tool_result_handler_projects_llm_message_artifacts_and_event() -> None:
    registry = ToolResultHandlerRegistry()
    lineage = RunLineage(session_id="session-1", run_id="run-1", root_run_id="run-1")
    result = ToolExecutionResult(
        tool_name="search",
        tool_call_id="tool-1",
        status=ToolExecutionStatus.succeeded,
        content="result text",
        artifacts=[ArtifactRef(id="artifact-1", uri="s3://bucket/key")],
    )

    projection = registry.handle(result, lineage=lineage, sequence_no=3)

    assert projection.llm_message.role == MessageRole.tool
    assert projection.llm_message.content == "result text"
    assert projection.llm_message.tool_call_id == "tool-1"
    assert projection.artifacts[0].uri == "s3://bucket/key"
    assert projection.domain_events[0].type == "ToolCallCompleted"
    assert projection.domain_events[0].payload["artifact_ids"] == ["artifact-1"]


def test_file_tool_handler_keeps_large_objects_as_artifact_refs() -> None:
    registry = build_default_tool_result_registry()
    lineage = RunLineage(session_id="session-1", run_id="run-1", root_run_id="run-1")
    result = ToolExecutionResult(
        tool_name="file_write",
        tool_call_id="tool-2",
        status=ToolExecutionStatus.succeeded,
        artifacts=[
            ArtifactRef(
                id="artifact-2",
                uri="s3://bucket/report.md",
                media_type="text/markdown",
                hash="sha256:abc",
            )
        ],
    )

    projection = registry.handle(result, lineage=lineage, sequence_no=4)

    assert projection.llm_message.content == "File tool produced 1 artifact reference(s)."
    assert projection.artifacts[0].uri == "s3://bucket/report.md"
    assert not hasattr(projection.artifacts[0], "body")


def test_registry_allows_new_tool_handler_without_runner_branch() -> None:
    registry = ToolResultHandlerRegistry()
    registry.register("custom_file", FileToolResultHandler())
    lineage = RunLineage(session_id="session-1", run_id="run-1", root_run_id="run-1")

    projection = registry.handle(
        ToolExecutionResult(
            tool_name="custom_file",
            tool_call_id="tool-3",
            status=ToolExecutionStatus.failed,
            error="permission denied",
        ),
        lineage=lineage,
        sequence_no=5,
    )

    assert projection.llm_message.content == "Tool failed: permission denied"
    assert projection.domain_events[0].type == "ToolCallFailed"
