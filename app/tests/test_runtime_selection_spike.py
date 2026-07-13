from __future__ import annotations

import uuid

import pytest
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.infrastructure.graph.runtime_selection_spike import (
    InMemoryIdempotencyLedger,
    RuntimeSpikeState,
    build_runtime_selection_spike_graph,
)


def config(thread_id: str | None = None) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}


def initial(thread_id: str, run_id: str) -> RuntimeSpikeState:
    return {
        "thread_id": thread_id,
        "run_id": run_id,
        "user_input": "research the source",
    }


def test_interrupt_resume_executes_side_effect_once() -> None:
    ledger = InMemoryIdempotencyLedger()
    graph = build_runtime_selection_spike_graph(side_effects=ledger)
    thread_id = str(uuid.uuid4())
    runtime_config = config(thread_id)

    interrupted = graph.invoke(initial(thread_id, "run-1"), runtime_config)
    assert "__interrupt__" in interrupted

    completed = graph.invoke(Command(resume="approved"), runtime_config)
    assert completed["completed"] is True
    assert completed["graph_version"] == "runtime-spike-v1"
    assert ledger.physical_executions == 1


def test_retry_after_crash_after_commit_does_not_repeat_effect() -> None:
    ledger = InMemoryIdempotencyLedger(crash_after_commit_once=True)
    graph = build_runtime_selection_spike_graph(side_effects=ledger)
    thread_id = str(uuid.uuid4())
    runtime_config = config(thread_id)

    graph.invoke(initial(thread_id, "run-crash"), runtime_config)
    with pytest.raises(RuntimeError, match="injected crash"):
        graph.invoke(Command(resume="approved"), runtime_config)

    completed = graph.invoke(None, runtime_config)
    assert completed["completed"] is True
    assert ledger.physical_executions == 1


def test_two_threads_do_not_share_checkpoint_or_operation_id() -> None:
    ledger = InMemoryIdempotencyLedger()
    checkpointer = InMemorySaver()
    graph = build_runtime_selection_spike_graph(
        side_effects=ledger,
        checkpointer=checkpointer,
    )

    for thread_id, run_id in (("thread-a", "run-a"), ("thread-b", "run-b")):
        runtime_config = config(thread_id)
        graph.invoke(initial(thread_id, run_id), runtime_config)
        completed = graph.invoke(Command(resume="approved"), runtime_config)
        assert completed["operation_id"] == f"{run_id}:side-effect"

    assert ledger.physical_executions == 2


def test_waiting_checkpoint_resumes_with_pinned_graph_version() -> None:
    ledger = InMemoryIdempotencyLedger()
    checkpointer = InMemorySaver()
    thread_id = str(uuid.uuid4())
    runtime_config = config(thread_id)
    old_graph = build_runtime_selection_spike_graph(
        side_effects=ledger,
        checkpointer=checkpointer,
        graph_version="runtime-spike-v1",
    )

    old_graph.invoke(initial(thread_id, "run-versioned"), runtime_config)

    # A newly deployed builder may have a different default, but a waiting run
    # must be routed to its pinned graph definition. This test records that
    # requirement by resuming with the old builder/checkpoint combination.
    completed = old_graph.invoke(Command(resume="approved"), runtime_config)
    assert completed["graph_version"] == "runtime-spike-v1"
    assert completed["completed"] is True
