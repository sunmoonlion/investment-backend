"""Disposable graph used only by V5-P0-001 runtime comparison.

This module is deliberately not imported by the API or worker.  Every runtime
candidate must execute the same graph semantics before ADR-001 can be accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NotRequired, Protocol, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class RuntimeSpikeState(TypedDict):
    thread_id: str
    run_id: str
    user_input: NotRequired[str]
    approval: NotRequired[str]
    operation_id: NotRequired[str]
    side_effect_result: NotRequired[str]
    graph_version: NotRequired[str]
    completed: NotRequired[bool]


class RuntimeSpikeUpdate(TypedDict, total=False):
    approval: str
    operation_id: str
    side_effect_result: str
    graph_version: str
    completed: bool


class SideEffectPort(Protocol):
    def execute_once(self, operation_id: str) -> str: ...


@dataclass
class InMemoryIdempotencyLedger:
    """Test double for a durable operation journal, not a production store."""

    results: dict[str, str] = field(default_factory=dict)
    physical_executions: int = 0
    crash_after_commit_once: bool = False
    _crashed: bool = False

    def execute_once(self, operation_id: str) -> str:
        if operation_id in self.results:
            return self.results[operation_id]

        self.physical_executions += 1
        result = f"effect:{operation_id}"
        self.results[operation_id] = result
        if self.crash_after_commit_once and not self._crashed:
            self._crashed = True
            raise RuntimeError("injected crash after side-effect commit")
        return result


def build_runtime_selection_spike_graph(
    *,
    side_effects: SideEffectPort,
    checkpointer: Any | None = None,
    graph_version: str = "runtime-spike-v1",
):
    """Build the runtime-neutral graph specified by ADR-001 section 5."""

    def persist_input(state: RuntimeSpikeState) -> RuntimeSpikeUpdate:
        if not state.get("thread_id") or not state.get("run_id"):
            raise ValueError("thread_id and run_id are required")
        return {
            "operation_id": f"{state['run_id']}:side-effect",
            "graph_version": graph_version,
        }

    def ask_user(state: RuntimeSpikeState) -> RuntimeSpikeUpdate:
        if state.get("approval"):
            return {}
        operation_id = state.get("operation_id")
        version = state.get("graph_version")
        if not operation_id or not version:
            raise RuntimeError("persisted operation and graph version are required")
        approval = interrupt(
            {
                "kind": "approval_required",
                "operation_id": operation_id,
                "graph_version": version,
            }
        )
        return {"approval": str(approval)}

    def side_effect_tool(state: RuntimeSpikeState) -> RuntimeSpikeUpdate:
        operation_id = state.get("operation_id")
        if not operation_id:
            raise RuntimeError("operation id is required")
        return {
            "side_effect_result": side_effects.execute_once(operation_id),
        }

    def final(state: RuntimeSpikeState) -> RuntimeSpikeUpdate:
        if not state.get("side_effect_result"):
            raise RuntimeError("side effect result is required")
        return {"completed": True}

    graph = StateGraph(RuntimeSpikeState)
    graph.add_node("persist_input", persist_input)
    graph.add_node("ask_user", ask_user)
    graph.add_node("side_effect_tool", side_effect_tool)
    graph.add_node("final", final)
    graph.add_edge(START, "persist_input")
    graph.add_edge("persist_input", "ask_user")
    graph.add_edge("ask_user", "side_effect_tool")
    graph.add_edge("side_effect_tool", "final")
    graph.add_edge("final", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
