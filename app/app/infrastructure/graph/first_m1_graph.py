from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.domain.agent.models import MessageRole, StoredMessage
from app.domain.agent.runtime import RunBudget
from app.infrastructure.graph.state import (
    PlannerReactState,
    validate_base_state_layering,
)


def normalize_input_node(state: PlannerReactState) -> PlannerReactState:
    validate_base_state_layering(state)
    user_input = state.get("user_input", {})
    text = str(user_input.get("text") or "")
    existing_messages = list(state.get("messages", []))
    if text:
        existing_messages.append(
            StoredMessage(
                role=MessageRole.user,
                content=text,
                sequence_no=len(existing_messages) + 1,
            )
        )
    return {"messages": existing_messages, "status": "planning"}


def create_or_update_plan_node(state: PlannerReactState) -> PlannerReactState:
    budget = RunBudget.model_validate(state.get("budget") or {})
    error = budget.consume_step().check()
    if error:
        return {"status": "budget_exceeded", "error": error.as_run_error()}

    plan = {
        "id": f"plan:{state.get('run_id', 'unknown')}",
        "version": 1,
        "steps": [
            {
                "id": "step-1",
                "title": "Respond to the user input",
                "status": "ready",
            }
        ],
    }
    return {
        "plan": plan,
        "current_step_id": "step-1",
        "current_step": plan["steps"][0],
        "budget": budget.consume_step().model_dump(),
        "status": "executing",
    }


def summarize_node(state: PlannerReactState) -> PlannerReactState:
    if state.get("status") == "budget_exceeded":
        return {}

    messages = list(state.get("messages", []))
    user_text = messages[-1].content if messages else ""
    messages.append(
        StoredMessage(
            role=MessageRole.assistant,
            content=f"m1-first-graph:{user_text}",
            sequence_no=len(messages) + 1,
        )
    )
    plan = dict(state.get("plan") or {})
    steps = list(plan.get("steps") or [])
    if steps:
        steps[0] = {**steps[0], "status": "completed"}
        plan["steps"] = steps
        plan["version"] = int(plan.get("version") or 1) + 1
    return {"messages": messages, "plan": plan, "status": "completed"}


def build_first_m1_graph(checkpointer: Any | None = None):
    graph = StateGraph(PlannerReactState)
    graph.add_node("normalize_input", normalize_input_node)
    graph.add_node("create_or_update_plan", create_or_update_plan_node)
    graph.add_node("summarize", summarize_node)
    graph.add_edge(START, "normalize_input")
    graph.add_edge("normalize_input", "create_or_update_plan")
    graph.add_edge("create_or_update_plan", "summarize")
    graph.add_edge("summarize", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())
