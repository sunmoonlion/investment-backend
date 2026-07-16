from __future__ import annotations

import uuid

import pytest
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.infrastructure.graph.runtime_selection_spike import (
    InMemoryDurableDispatchJournal,
    InMemoryIdempotencyLedger,
    InMemoryRuntimeControlPlane,
    RuntimeSpikeCancelled,
    RuntimeSpikeRejected,
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


def test_same_thread_rejects_second_non_terminal_run() -> None:
    ledger = InMemoryIdempotencyLedger()
    control_plane = InMemoryRuntimeControlPlane()
    checkpointer = InMemorySaver()
    graph = build_runtime_selection_spike_graph(
        side_effects=ledger,
        checkpointer=checkpointer,
        control_plane=control_plane,
    )

    graph.invoke(initial("shared-thread", "run-a"), config("checkpoint-a"))

    with pytest.raises(RuntimeSpikeRejected, match="thread already has active run"):
        graph.invoke(initial("shared-thread", "run-b"), config("checkpoint-b"))

    completed = graph.invoke(Command(resume="approved"), config("checkpoint-a"))
    assert completed["completed"] is True

    graph.invoke(initial("shared-thread", "run-b"), config("checkpoint-b"))
    assert control_plane.runs["run-b"].status == "waiting"


def test_cancelled_run_stops_before_side_effect_and_releases_thread() -> None:
    ledger = InMemoryIdempotencyLedger()
    control_plane = InMemoryRuntimeControlPlane()
    graph = build_runtime_selection_spike_graph(
        side_effects=ledger,
        control_plane=control_plane,
    )
    runtime_config = config("cancel-checkpoint")

    graph.invoke(initial("cancel-thread", "run-cancel"), runtime_config)
    control_plane.request_cancel("run-cancel")

    with pytest.raises(RuntimeSpikeCancelled, match="run cancelled"):
        graph.invoke(Command(resume="approved"), runtime_config)

    assert control_plane.runs["run-cancel"].status == "cancelled"
    assert ledger.physical_executions == 0

    replacement = build_runtime_selection_spike_graph(
        side_effects=ledger,
        control_plane=control_plane,
    )
    replacement.invoke(
        initial("cancel-thread", "run-after-cancel"),
        config("replacement-checkpoint"),
    )
    assert control_plane.runs["run-after-cancel"].status == "waiting"


def test_cursor_reconciliation_returns_every_event_after_disconnect() -> None:
    ledger = InMemoryIdempotencyLedger()
    control_plane = InMemoryRuntimeControlPlane()
    graph = build_runtime_selection_spike_graph(
        side_effects=ledger,
        control_plane=control_plane,
    )
    runtime_config = config("cursor-checkpoint")

    graph.invoke(initial("cursor-thread", "run-cursor"), runtime_config)
    disconnected_after = control_plane.events[-1].cursor
    graph.invoke(Command(resume="approved"), runtime_config)

    recovered = control_plane.events_after(disconnected_after)
    assert [event.type for event in recovered] == [
        "RunStarted",
        "RunCompleted",
    ]
    assert [event.cursor for event in recovered] == list(
        range(disconnected_after + 1, disconnected_after + 1 + len(recovered))
    )


def test_broker_failure_keeps_durable_dispatch_intent_for_retry() -> None:
    journal = InMemoryDurableDispatchJournal()
    sent: list[str] = []
    journal.record("run-dispatch")

    def unavailable_broker(run_id: str) -> str:
        raise ConnectionError(f"broker unavailable for {run_id}")

    with pytest.raises(ConnectionError, match="broker unavailable"):
        journal.dispatch("run-dispatch", unavailable_broker)

    assert journal.pending_run_ids() == ["run-dispatch"]
    assert journal.intents["run-dispatch"].attempts == 1

    def recovered_broker(run_id: str) -> str:
        sent.append(run_id)
        return "task-1"

    assert journal.dispatch("run-dispatch", recovered_broker) == "task-1"
    assert journal.dispatch("run-dispatch", recovered_broker) == "task-1"
    assert sent == ["run-dispatch"]
    assert journal.pending_run_ids() == []
    assert journal.intents["run-dispatch"].attempts == 2
