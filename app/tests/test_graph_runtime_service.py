from __future__ import annotations

import uuid

from app.application.agent.graph_runtime_service import GraphRuntimeService
from app.infrastructure.graph.langgraph_runtime import LangGraphRuntimeService
from app.infrastructure.graph.walking_skeleton import build_walking_skeleton_graph


def test_graph_runtime_builds_thread_config_from_session_id() -> None:
    service = GraphRuntimeService()

    assert service.build_config(session_id="session-1") == {
        "configurable": {"thread_id": "session-1"}
    }


def test_graph_runtime_reports_interrupt_and_resume_state() -> None:
    service = LangGraphRuntimeService()
    session_id = str(uuid.uuid4())
    graph = build_walking_skeleton_graph()

    interrupted = service.run(
        graph,
        {"session_id": session_id, "run_id": "run-1"},
        session_id=session_id,
    )

    assert interrupted.interrupted
    assert "__interrupt__" in interrupted.state

    resumed = service.resume(graph, "continue", session_id=session_id)

    assert not resumed.interrupted
    assert resumed.state == {"user_input": "continue", "side_effect_done": True}
