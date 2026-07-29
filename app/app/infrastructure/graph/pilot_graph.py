"""Isolated P0-008C graph candidate.

This graph is deliberately separate from both the Phase-0 Walking Skeleton and
the disposable ADR-001 comparison spike.  It proves the browser/Runtime product
contract in an isolated deployment; it is not the M1 durable runner.
"""

from __future__ import annotations

import uuid
from typing import Any, NotRequired, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

PILOT_GRAPH_NAME = "research_web_pilot"
PILOT_GRAPH_VERSION = "p0-008c-v1"


class PilotGraphState(TypedDict):
    run_id: str
    user_input: str
    draft: str
    citations: list[dict[str, Any]]
    action_id: NotRequired[str]
    approval: NotRequired[str]
    summary: NotRequired[str]
    completed: NotRequired[bool]


class PilotGraphUpdate(TypedDict, total=False):
    action_id: str
    approval: str
    summary: str
    completed: bool


def approval_action_id(run_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sunmoonai:p0-008c:{run_id}:approval"))


def build_pilot_graph(checkpointer: Any | None = None):
    def prepare(state: PilotGraphState) -> PilotGraphUpdate:
        if not state.get("run_id") or not state.get("user_input"):
            raise ValueError("run_id and user_input are required")
        if not state.get("draft"):
            raise ValueError("a real provider draft is required")
        if not state.get("citations"):
            raise ValueError("at least one real citation is required")
        return {"action_id": approval_action_id(state["run_id"])}

    def request_approval(state: PilotGraphState) -> PilotGraphUpdate:
        if state.get("approval"):
            return {}
        action_id = state.get("action_id")
        if not action_id:
            raise ValueError("approval action_id is required")
        value = interrupt(
            {
                "kind": "confirmation",
                "action_id": action_id,
                "prompt": "确认使用这些检索证据生成最终回答？",
            }
        )
        return {"approval": str(value)}

    def finalize(state: PilotGraphState) -> PilotGraphUpdate:
        approval = state.get("approval", "").strip()
        if not approval:
            raise ValueError("approval is required")
        if approval.lower() in {"reject", "cancel", "取消", "拒绝"}:
            return {
                "summary": "用户拒绝了候选回答。",
                "completed": True,
            }
        return {"summary": state["draft"], "completed": True}

    graph = StateGraph(PilotGraphState)
    graph.add_node("prepare", prepare)
    graph.add_node("request_approval", request_approval)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "request_approval")
    graph.add_edge("request_approval", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
