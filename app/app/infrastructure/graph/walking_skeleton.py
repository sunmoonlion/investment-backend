from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class WalkingSkeletonState(TypedDict, total=False):
    session_id: str
    run_id: str
    user_input: str
    side_effect_done: bool


def ask_user_node(state: WalkingSkeletonState) -> WalkingSkeletonState:
    if not state.get("user_input"):
        answer = interrupt(
            {
                "kind": "ask_user",
                "question": "Please provide input for the Phase 0 skeleton.",
            }
        )
        return {"user_input": str(answer)}
    return {}


def side_effect_tool_node(state: WalkingSkeletonState) -> WalkingSkeletonState:
    return {"side_effect_done": True}


def build_walking_skeleton_graph(checkpointer: Any | None = None):
    graph = StateGraph(WalkingSkeletonState)
    graph.add_node("ask_user_node", ask_user_node)
    graph.add_node("side_effect_tool_node", side_effect_tool_node)
    graph.add_edge(START, "ask_user_node")
    graph.add_edge("ask_user_node", "side_effect_tool_node")
    graph.add_edge("side_effect_tool_node", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
