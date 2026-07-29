from __future__ import annotations

from collections.abc import Callable

from app.domain.agent.models import DomainEvent, UIEvent

ProjectorHandler = Callable[[DomainEvent], UIEvent]


def _timeline_event(ui_type: str) -> ProjectorHandler:
    def handler(event: DomainEvent) -> UIEvent:
        return UIEvent(
            type=ui_type,
            payload=event.payload,
            lineage=event.lineage,
            schema_version=event.schema_version,
        )

    return handler


def _default_handler(event: DomainEvent) -> UIEvent:
    return UIEvent(
        type=event.type,
        payload=event.payload,
        lineage=event.lineage,
        schema_version=event.schema_version,
    )


class TimelineProjector:
    def __init__(self, handlers: dict[str, ProjectorHandler] | None = None):
        self.handlers = {**DEFAULT_TIMELINE_HANDLERS, **(handlers or {})}

    def project(self, event: DomainEvent) -> UIEvent:
        return self.handlers.get(event.type, _default_handler)(event)


DEFAULT_TIMELINE_HANDLERS: dict[str, ProjectorHandler] = {
    "RunStarted": _timeline_event("TimelineRunStarted"),
    "HumanInputRequested": _timeline_event("TimelineWaitInputDisplayed"),
    "UserInputReceived": _timeline_event("TimelineUserInputReceived"),
    "ToolCallStarted": _timeline_event("TimelineToolStarted"),
    "ToolCallCompleted": _timeline_event("TimelineToolCompleted"),
    "RunCompleted": _timeline_event("TimelineRunCompleted"),
    "RunFailed": _timeline_event("TimelineRunFailed"),
}
