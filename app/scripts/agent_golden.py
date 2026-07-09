from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.types import Command

from app.application.agent.timeline_projector import TimelineProjector
from app.domain.agent.models import DomainEvent, RunLineage
from app.infrastructure.graph.first_m1_graph import build_first_m1_graph
from app.infrastructure.graph.langgraph_runtime import LangGraphRuntimeService
from app.infrastructure.graph.walking_skeleton import build_walking_skeleton_graph
from app.tasks.agent_graph import _stream_graph


@dataclass(frozen=True)
class GoldenCaseResult:
    name: str
    timeline: list[str]
    resumed_state: dict[str, Any]
    llm_calls: int


def load_golden_case(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected={expected!r} actual={actual!r}")


def _message_field(message: Any, field: str) -> Any:
    if isinstance(message, dict):
        return message.get(field)
    return getattr(message, field)


def run_phase0_golden_case(case: dict[str, Any]) -> GoldenCaseResult:
    graph_case = case["graph"]
    initial_state = graph_case["initial_state"]
    resume_input = graph_case["resume_input"]

    graph = build_walking_skeleton_graph()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    interrupted = _stream_graph(graph, initial_state, config)
    if "__interrupt__" not in interrupted:
        raise AssertionError("golden graph did not interrupt before user input")

    resumed = _stream_graph(graph, Command(resume=resume_input), config)
    assert_equal(
        resumed,
        graph_case["expected_resumed_state"],
        "golden resumed graph state mismatch",
    )

    lineage = RunLineage(
        session_id=initial_state["session_id"],
        run_id=initial_state["run_id"],
        root_run_id=initial_state["run_id"],
    )
    projector = TimelineProjector()
    timeline: list[str] = []
    for event_case in case["events"]:
        event = DomainEvent(
            type=event_case["domain_type"],
            payload=event_case["payload"],
            lineage=lineage,
        )
        ui_event = projector.project(event)
        assert_equal(
            ui_event.type,
            event_case["expected_ui_type"],
            f"golden projection mismatch for {event.type}",
        )
        assert_equal(
            ui_event.payload,
            event.payload,
            f"golden payload mismatch for {event.type}",
        )
        timeline.append(ui_event.type)

    assert_equal(timeline, case["expected_timeline"], "golden timeline mismatch")
    llm_calls = int(case.get("llm", {}).get("expected_calls", 0))
    assert_equal(llm_calls, 0, "phase0 golden case must not require live LLM calls")

    return GoldenCaseResult(
        name=case["name"],
        timeline=timeline,
        resumed_state=resumed,
        llm_calls=llm_calls,
    )


def run_first_m1_graph_golden_case(case: dict[str, Any]) -> GoldenCaseResult:
    graph_case = case["graph"]
    initial_state = graph_case["initial_state"]
    result = LangGraphRuntimeService().run(
        build_first_m1_graph(),
        initial_state,
        session_id=initial_state["session_id"],
    )

    messages = [
        {
            "role": message.role,
            "content": message.content,
            "sequence_no": message.sequence_no,
        }
        for message in result.state["messages"]
    ]
    normalized_state = {
        "status": result.state["status"],
        "plan": result.state["plan"],
        "current_step_id": result.state["current_step_id"],
        "messages": messages,
    }
    assert_equal(
        normalized_state,
        graph_case["expected_state"],
        "first M1 graph golden state mismatch",
    )
    llm_calls = int(case.get("llm", {}).get("expected_calls", 0))
    assert_equal(llm_calls, 0, "first M1 graph golden case must not require live LLM calls")

    return GoldenCaseResult(
        name=case["name"],
        timeline=[],
        resumed_state=normalized_state,
        llm_calls=llm_calls,
    )


def run_old_project_behavior_reference_case(case: dict[str, Any]) -> GoldenCaseResult:
    graph_case = case["graph"]
    contract = graph_case["behavior_contract"]
    initial_state = graph_case["initial_state"]
    result = LangGraphRuntimeService().run(
        build_first_m1_graph(),
        initial_state,
        session_id=initial_state["session_id"],
    )
    state = result.state

    if contract.get("requires_plan") and not state.get("plan"):
        raise AssertionError("old-project behavior reference requires a plan")

    steps = list(state.get("plan", {}).get("steps") or [])
    minimum_steps = int(contract.get("minimum_steps", 0))
    if len(steps) < minimum_steps:
        raise AssertionError(
            f"old-project behavior reference requires at least {minimum_steps} step(s)"
        )

    if contract.get("requires_completed_step") and not any(
        step.get("status") == "completed" for step in steps
    ):
        raise AssertionError("old-project behavior reference requires a completed step")

    messages = list(state.get("messages") or [])
    assistant_messages = [
        message
        for message in messages
        if _message_field(message, "role") == "assistant"
    ]
    if contract.get("requires_assistant_message") and not assistant_messages:
        raise AssertionError("old-project behavior reference requires an assistant message")

    prefix = contract.get("assistant_message_prefix")
    if prefix:
        last_assistant = assistant_messages[-1]
        content = _message_field(last_assistant, "content")
        if not str(content).startswith(prefix):
            raise AssertionError(
                f"old-project behavior reference requires assistant prefix {prefix!r}"
            )

    normalized_state = {
        "status": state["status"],
        "plan_step_count": len(steps),
        "completed_step_count": len(
            [step for step in steps if step.get("status") == "completed"]
        ),
        "assistant_message_count": len(assistant_messages),
    }
    llm_calls = int(case.get("llm", {}).get("expected_calls", 0))
    assert_equal(llm_calls, 0, "old-project behavior reference must not require live LLM calls")

    return GoldenCaseResult(
        name=case["name"],
        timeline=[],
        resumed_state=normalized_state,
        llm_calls=llm_calls,
    )


def run_golden_case(case: dict[str, Any]) -> GoldenCaseResult:
    case_type = case.get("type", "phase0_walking_skeleton")
    if case_type == "phase0_walking_skeleton":
        return run_phase0_golden_case(case)
    if case_type == "first_m1_graph":
        return run_first_m1_graph_golden_case(case)
    if case_type == "old_project_behavior_reference":
        return run_old_project_behavior_reference_case(case)
    raise ValueError(f"unknown golden case type: {case_type}")
