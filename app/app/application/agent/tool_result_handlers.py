from __future__ import annotations

from app.domain.agent.models import DomainEvent, MessageRole, RunLineage, StoredMessage
from app.domain.agent.tools import (
    ToolExecutionResult,
    ToolExecutionStatus,
    ToolResultHandler,
    ToolResultProjection,
)


class DefaultToolResultHandler:
    def handle(
        self,
        result: ToolExecutionResult,
        *,
        lineage: RunLineage,
        sequence_no: int,
    ) -> ToolResultProjection:
        event_type = (
            "ToolCallCompleted"
            if result.status == ToolExecutionStatus.succeeded
            else "ToolCallFailed"
        )
        content = result.content
        if result.error:
            content = f"Tool failed: {result.error}"
        return ToolResultProjection(
            llm_message=StoredMessage(
                role=MessageRole.tool,
                content=content,
                sequence_no=sequence_no,
                tool_call_id=result.tool_call_id,
                metadata={"tool_name": result.tool_name, "status": result.status.value},
            ),
            artifacts=result.artifacts,
            domain_events=[
                DomainEvent(
                    type=event_type,
                    payload={
                        "tool_name": result.tool_name,
                        "tool_call_id": result.tool_call_id,
                        "status": result.status.value,
                        "artifact_ids": [artifact.id for artifact in result.artifacts],
                    },
                    lineage=lineage,
                )
            ],
        )


class FileToolResultHandler(DefaultToolResultHandler):
    def handle(
        self,
        result: ToolExecutionResult,
        *,
        lineage: RunLineage,
        sequence_no: int,
    ) -> ToolResultProjection:
        projection = super().handle(result, lineage=lineage, sequence_no=sequence_no)
        if result.artifacts:
            projection.llm_message.content = (
                f"File tool produced {len(result.artifacts)} artifact reference(s)."
            )
        return projection


class ToolResultHandlerRegistry:
    def __init__(
        self,
        handlers: dict[str, ToolResultHandler] | None = None,
        default_handler: ToolResultHandler | None = None,
    ):
        self.handlers = handlers or {}
        self.default_handler = default_handler or DefaultToolResultHandler()

    def register(self, tool_name: str, handler: ToolResultHandler) -> None:
        self.handlers[tool_name] = handler

    def handle(
        self,
        result: ToolExecutionResult,
        *,
        lineage: RunLineage,
        sequence_no: int,
    ) -> ToolResultProjection:
        handler = self.handlers.get(result.tool_name, self.default_handler)
        return handler.handle(result, lineage=lineage, sequence_no=sequence_no)


def build_default_tool_result_registry() -> ToolResultHandlerRegistry:
    return ToolResultHandlerRegistry(
        handlers={
            "file_read": FileToolResultHandler(),
            "file_write": FileToolResultHandler(),
        }
    )
