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


class RuntimeControlPlane(Protocol):
    def create_run(self, *, thread_id: str, run_id: str, graph_version: str) -> None: ...

    def mark_running(self, run_id: str) -> None: ...

    def mark_waiting(self, run_id: str) -> None: ...

    def raise_if_cancelled(self, run_id: str) -> None: ...

    def complete(self, run_id: str) -> None: ...


class RuntimeSpikeRejected(RuntimeError):
    """Raised when the selected thread concurrency policy rejects a run."""


class RuntimeSpikeCancelled(RuntimeError):
    """Raised at a cooperative cancellation boundary."""


@dataclass(frozen=True)
class RuntimeSpikeEvent:
    cursor: int
    type: str
    thread_id: str
    run_id: str


@dataclass
class RuntimeSpikeRun:
    thread_id: str
    run_id: str
    graph_version: str
    status: str = "created"
    cancel_requested: bool = False


@dataclass
class InMemoryRuntimeControlPlane:
    """Reference semantics for candidate A, not a production repository.

    The spike freezes three externally observable rules before the production
    data model is built:

    * one non-terminal run per thread, with a deterministic reject policy;
    * cooperative cancellation is durable before graph execution continues;
    * durable events use a monotonically increasing cursor for reconciliation.
    """

    runs: dict[str, RuntimeSpikeRun] = field(default_factory=dict)
    active_by_thread: dict[str, str] = field(default_factory=dict)
    events: list[RuntimeSpikeEvent] = field(default_factory=list)
    _next_cursor: int = 1

    def create_run(self, *, thread_id: str, run_id: str, graph_version: str) -> None:
        existing = self.runs.get(run_id)
        if existing:
            if (
                existing.thread_id != thread_id
                or existing.graph_version != graph_version
            ):
                raise RuntimeSpikeRejected("run identity cannot be rebound")
            return

        active_run_id = self.active_by_thread.get(thread_id)
        if active_run_id:
            active = self.runs[active_run_id]
            if active.status not in {"completed", "cancelled", "failed"}:
                raise RuntimeSpikeRejected(
                    f"thread already has active run: {active_run_id}"
                )

        self.runs[run_id] = RuntimeSpikeRun(
            thread_id=thread_id,
            run_id=run_id,
            graph_version=graph_version,
        )
        self.active_by_thread[thread_id] = run_id
        self._append("RunCreated", self.runs[run_id])

    def mark_running(self, run_id: str) -> None:
        run = self._run(run_id)
        if run.cancel_requested or run.status in {"cancelled", "completed"}:
            return
        self._transition(run, "running", "RunStarted")

    def mark_waiting(self, run_id: str) -> None:
        run = self._run(run_id)
        if run.cancel_requested or run.status in {"cancelled", "completed"}:
            return
        self._transition(run, "waiting", "RunWaiting")

    def request_cancel(self, run_id: str) -> None:
        run = self._run(run_id)
        if run.status in {"completed", "cancelled", "failed"}:
            return
        if not run.cancel_requested:
            run.cancel_requested = True
            self._append("RunCancelRequested", run)

    def raise_if_cancelled(self, run_id: str) -> None:
        run = self._run(run_id)
        if not run.cancel_requested:
            return
        self._transition(run, "cancelled", "RunCancelled")
        self.active_by_thread.pop(run.thread_id, None)
        raise RuntimeSpikeCancelled(f"run cancelled: {run_id}")

    def complete(self, run_id: str) -> None:
        run = self._run(run_id)
        self.raise_if_cancelled(run_id)
        self._transition(run, "completed", "RunCompleted")
        self.active_by_thread.pop(run.thread_id, None)

    def events_after(self, cursor: int = 0) -> list[RuntimeSpikeEvent]:
        return [event for event in self.events if event.cursor > cursor]

    def _run(self, run_id: str) -> RuntimeSpikeRun:
        try:
            return self.runs[run_id]
        except KeyError as exc:
            raise RuntimeError(f"unknown runtime spike run: {run_id}") from exc

    def _transition(
        self,
        run: RuntimeSpikeRun,
        status: str,
        event_type: str,
    ) -> None:
        if run.status == status:
            return
        run.status = status
        self._append(event_type, run)

    def _append(self, event_type: str, run: RuntimeSpikeRun) -> None:
        self.events.append(
            RuntimeSpikeEvent(
                cursor=self._next_cursor,
                type=event_type,
                thread_id=run.thread_id,
                run_id=run.run_id,
            )
        )
        self._next_cursor += 1


@dataclass
class RuntimeDispatchIntent:
    run_id: str
    status: str = "pending"
    attempts: int = 0
    task_id: str | None = None


@dataclass
class InMemoryDurableDispatchJournal:
    """Reference outbox semantics for a broker outage in candidate A."""

    intents: dict[str, RuntimeDispatchIntent] = field(default_factory=dict)

    def record(self, run_id: str) -> None:
        self.intents.setdefault(run_id, RuntimeDispatchIntent(run_id=run_id))

    def dispatch(self, run_id: str, send: Any) -> str | None:
        intent = self.intents[run_id]
        if intent.status == "dispatched":
            return intent.task_id
        intent.attempts += 1
        try:
            task_id = str(send(run_id))
        except Exception:
            intent.status = "pending"
            raise
        intent.status = "dispatched"
        intent.task_id = task_id
        return task_id

    def pending_run_ids(self) -> list[str]:
        return [
            run_id
            for run_id, intent in self.intents.items()
            if intent.status == "pending"
        ]


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
    control_plane: RuntimeControlPlane | None = None,
):
    """Build the runtime-neutral graph specified by ADR-001 section 5."""

    def persist_input(state: RuntimeSpikeState) -> RuntimeSpikeUpdate:
        if not state.get("thread_id") or not state.get("run_id"):
            raise ValueError("thread_id and run_id are required")
        if control_plane:
            control_plane.create_run(
                thread_id=state["thread_id"],
                run_id=state["run_id"],
                graph_version=graph_version,
            )
            control_plane.mark_running(state["run_id"])
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
        if control_plane:
            control_plane.mark_waiting(state["run_id"])
        approval = interrupt(
            {
                "kind": "approval_required",
                "operation_id": operation_id,
                "graph_version": version,
            }
        )
        if control_plane:
            control_plane.mark_running(state["run_id"])
        return {"approval": str(approval)}

    def side_effect_tool(state: RuntimeSpikeState) -> RuntimeSpikeUpdate:
        operation_id = state.get("operation_id")
        if not operation_id:
            raise RuntimeError("operation id is required")
        if control_plane:
            control_plane.raise_if_cancelled(state["run_id"])
        return {
            "side_effect_result": side_effects.execute_once(operation_id),
        }

    def final(state: RuntimeSpikeState) -> RuntimeSpikeUpdate:
        if not state.get("side_effect_result"):
            raise RuntimeError("side effect result is required")
        if control_plane:
            control_plane.complete(state["run_id"])
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
