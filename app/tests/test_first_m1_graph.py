from __future__ import annotations

import uuid

from app.infrastructure.graph.first_m1_graph import build_first_m1_graph, normalize_input_node
from app.infrastructure.graph.langgraph_runtime import LangGraphRuntimeService


def test_first_m1_graph_uses_base_state_and_completes() -> None:
    session_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    graph = build_first_m1_graph()

    result = LangGraphRuntimeService().run(
        graph,
        {
            "session_id": session_id,
            "run_id": run_id,
            "user_input": {"text": "hello"},
            "budget": {"max_steps": 3},
        },
        session_id=session_id,
    )

    assert not result.interrupted
    assert result.state["status"] == "completed"
    assert result.state["plan"]["id"] == f"plan:{run_id}"
    assert result.state["current_step_id"] == "step-1"
    assert [message.role for message in result.state["messages"]] == ["user", "assistant"]
    assert result.state["messages"][-1].content == "m1-first-graph:hello"


def test_first_m1_graph_returns_structured_budget_error() -> None:
    session_id = str(uuid.uuid4())
    graph = build_first_m1_graph()

    result = LangGraphRuntimeService().run(
        graph,
        {
            "session_id": session_id,
            "run_id": "run-1",
            "user_input": {"text": "hello"},
            "budget": {"max_steps": 1, "steps_used": 1},
        },
        session_id=session_id,
    )

    assert result.state["status"] == "budget_exceeded"
    assert result.state["error"]["code"] == "budget_exceeded"


def test_first_m1_graph_rejects_cross_layer_state() -> None:
    try:
        normalize_input_node({"session_id": "session-1", "run_id": "run-1", "event_history": []})
    except ValueError as exc:
        assert "cross-layer keys" in str(exc)
    else:
        raise AssertionError("first M1 graph node should reject cross-layer state")
