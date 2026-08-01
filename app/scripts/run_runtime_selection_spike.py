from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.runnables.config import RunnableConfig
from langgraph.types import Command

from app.infrastructure.graph.runtime_selection_spike import (
    InMemoryIdempotencyLedger,
    RuntimeSpikeState,
    build_runtime_selection_spike_graph,
)


def main() -> None:
    ledger = InMemoryIdempotencyLedger(crash_after_commit_once=True)
    graph = build_runtime_selection_spike_graph(side_effects=ledger)
    thread_id = str(uuid.uuid4())
    runtime_config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    graph_input: RuntimeSpikeState = {
        "thread_id": thread_id,
        "run_id": "custom-runtime-spike",
        "user_input": "runtime comparison",
    }

    interrupted = graph.invoke(graph_input, runtime_config)
    crashed = False
    try:
        graph.invoke(Command(resume="approved"), runtime_config)
    except RuntimeError as exc:
        if "injected crash" not in str(exc):
            raise
        crashed = True
    completed = graph.invoke(None, runtime_config)

    result = {
        "candidate": "A-custom-runtime",
        "interrupt": "__interrupt__" in interrupted,
        "injected_crash_observed": crashed,
        "resume_completed": completed.get("completed") is True,
        "graph_version": completed.get("graph_version"),
        "physical_side_effect_executions": ledger.physical_executions,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result != {
        "candidate": "A-custom-runtime",
        "interrupt": True,
        "injected_crash_observed": True,
        "resume_completed": True,
        "graph_version": "runtime-spike-v1",
        "physical_side_effect_executions": 1,
    }:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
