from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.runnables.config import RunnableConfig
from langgraph.types import Command

from app.infrastructure.graph.checkpointer import phase0_postgres_checkpointer
from app.infrastructure.graph.runtime_selection_spike import (
    InMemoryIdempotencyLedger,
    RuntimeSpikeState,
    build_runtime_selection_spike_graph,
)


def main() -> None:
    ledger = InMemoryIdempotencyLedger()
    thread_id = str(uuid.uuid4())
    runtime_config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    graph_input: RuntimeSpikeState = {
        "thread_id": thread_id,
        "run_id": f"postgres-spike-{thread_id}",
        "user_input": "cross-connection runtime comparison",
    }

    with phase0_postgres_checkpointer() as checkpointer:
        first_process_graph = build_runtime_selection_spike_graph(
            side_effects=ledger,
            checkpointer=checkpointer,
        )
        interrupted = first_process_graph.invoke(graph_input, runtime_config)

    # Closing and reopening the checkpointer models a worker process boundary.
    with phase0_postgres_checkpointer() as checkpointer:
        replacement_worker_graph = build_runtime_selection_spike_graph(
            side_effects=ledger,
            checkpointer=checkpointer,
        )
        completed = replacement_worker_graph.invoke(
            Command(resume="approved"),
            runtime_config,
        )

    result = {
        "candidate": "A-custom-runtime",
        "checkpoint": "postgres",
        "interrupt": "__interrupt__" in interrupted,
        "replacement_worker_resume_completed": completed.get("completed") is True,
        "physical_side_effect_executions": ledger.physical_executions,
        "thread_id": thread_id,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if not all(
        (
            result["interrupt"],
            result["replacement_worker_resume_completed"],
            result["physical_side_effect_executions"] == 1,
        )
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
