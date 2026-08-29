from __future__ import annotations

import uuid

from langgraph.types import Command

from app.infrastructure.graph.pilot_graph import (
    PILOT_GRAPH_VERSION,
    approval_action_id,
    build_pilot_graph,
)


def citation() -> dict[str, object]:
    evidence_id = uuid.uuid4()
    return {
        "contract_version": 1,
        "evidence_id": str(evidence_id),
        "knowledge_document_id": str(uuid.uuid4()),
        "knowledge_document_version_id": str(uuid.uuid4()),
        "chunk_id": str(uuid.uuid4()),
        "title": "Real retrieval title",
        "quote": "Bounded real evidence.",
        "source_document_id": str(uuid.uuid4()),
        "source_document_version_id": str(uuid.uuid4()),
        "content_hash": "a" * 64,
        "source_href": f"/api/web/v1/citations/{evidence_id}/source",
    }


def test_pilot_graph_interrupt_resume_uses_provider_draft_and_citation() -> None:
    run_id = str(uuid.uuid4())
    graph = build_pilot_graph()
    config = {"configurable": {"thread_id": run_id}}
    first = graph.invoke(
        {
            "run_id": run_id,
            "user_input": "What is in the source?",
            "draft": "Provider-backed answer.",
            "citations": [citation()],
        },
        config=config,
    )
    assert "__interrupt__" in first
    interrupt_payload = first["__interrupt__"][0].value
    assert interrupt_payload["action_id"] == approval_action_id(run_id)
    assert PILOT_GRAPH_VERSION == "p0-008c-v1"

    completed = graph.invoke(Command(resume="confirm"), config=config)
    assert completed["completed"] is True
    assert completed["summary"] == "Provider-backed answer."


def test_pilot_graph_fails_closed_without_real_inputs() -> None:
    graph = build_pilot_graph()
    run_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": run_id}}
    try:
        graph.invoke(
            {
                "run_id": run_id,
                "user_input": "question",
                "draft": "",
                "citations": [],
            },
            config=config,
        )
    except ValueError as exc:
        assert "real provider draft" in str(exc)
    else:
        raise AssertionError("pilot graph must reject a fake/empty provider result")
