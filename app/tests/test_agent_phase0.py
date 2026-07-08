from __future__ import annotations

import uuid

from langgraph.types import Command

from app.application.agent.timeline_projector import TimelineProjector
from app.domain.agent.models import DomainEvent, RunLineage, UIEvent
from app.infrastructure.graph.walking_skeleton import build_walking_skeleton_graph
from app.tasks.agent_graph import _stream_graph


def test_walking_skeleton_interrupts_and_resumes() -> None:
    graph = build_walking_skeleton_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    interrupted = _stream_graph(graph, {"session_id": "s1", "run_id": "r1"}, config)
    assert "__interrupt__" in interrupted

    resumed = _stream_graph(graph, Command(resume="continue"), config)
    assert resumed == {"user_input": "continue", "side_effect_done": True}


def test_timeline_projector_maps_domain_event_to_ui_event() -> None:
    lineage = RunLineage(session_id="s1", run_id="r1", root_run_id="r1")
    domain_event = DomainEvent(
        type="HumanInputRequested",
        payload={"resume_token": "phase0:r1"},
        lineage=lineage,
    )

    ui_event = TimelineProjector().project(domain_event)

    assert ui_event.type == "TimelineWaitInputDisplayed"
    assert ui_event.payload == {"resume_token": "phase0:r1"}
    assert ui_event.lineage == lineage


def test_timeline_projector_accepts_registered_handlers() -> None:
    lineage = RunLineage(session_id="s1", run_id="r1", root_run_id="r1")
    domain_event = DomainEvent(type="CustomFactRecorded", payload={"ok": True}, lineage=lineage)

    ui_event = TimelineProjector(
        handlers={
            "CustomFactRecorded": lambda event: UIEvent(
                type="TimelineCustomFactRecorded",
                payload=event.payload,
                lineage=event.lineage,
            )
        }
    ).project(domain_event)

    assert ui_event.type == "TimelineCustomFactRecorded"
    assert ui_event.payload == {"ok": True}
