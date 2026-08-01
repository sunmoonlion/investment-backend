from __future__ import annotations

import pytest

from app.domain.agent.runtime import (
    AgentError,
    AgentErrorCode,
    RunBudget,
    validate_run_status_transition,
)


def test_run_budget_allows_usage_at_limit() -> None:
    budget = RunBudget(max_steps=2, steps_used=2)

    assert budget.check() is None


def test_run_budget_returns_structured_error_when_exceeded() -> None:
    budget = RunBudget(max_tool_calls=1).consume_tool_call().consume_tool_call()

    error = budget.check()

    assert error is not None
    assert error.code == AgentErrorCode.budget_exceeded
    assert error.message == "run tool-call budget exceeded"
    assert error.details == {"budget": "tool_calls"}


def test_run_budget_consumers_are_immutable_updates() -> None:
    budget = RunBudget(max_steps=3)
    updated = budget.consume_step()

    assert budget.steps_used == 0
    assert updated.steps_used == 1


def test_agent_error_serializes_for_run_storage() -> None:
    error = AgentError(
        code=AgentErrorCode.tool_denied,
        message="tool is not allowed",
        details={"tool_name": "shell"},
    )

    assert error.as_run_error() == {
        "code": "tool_denied",
        "message": "tool is not allowed",
        "retryable": False,
        "details": {"tool_name": "shell"},
    }


def test_run_status_state_machine_allows_m1_lifecycle() -> None:
    validate_run_status_transition("created", "running")
    validate_run_status_transition("running", "waiting")
    validate_run_status_transition("waiting", "running")
    validate_run_status_transition("running", "completed")
    validate_run_status_transition("running", "budget_exceeded")


def test_run_status_state_machine_rejects_terminal_reopen() -> None:
    with pytest.raises(ValueError, match="invalid run status transition"):
        validate_run_status_transition("completed", "running")


def test_run_status_state_machine_rejects_unknown_status() -> None:
    with pytest.raises(ValueError):
        validate_run_status_transition("created", "missing")
