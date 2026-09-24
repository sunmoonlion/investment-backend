"""状态机纯逻辑：合法转换表、WAITING 必带原因、终态不可出、方向盘随终态交回。"""

from __future__ import annotations

import pytest

from app.domain.workbench.states import (
    ATTEMPT_TERMINAL,
    ATTEMPT_TRANSITIONS,
    TASK_TERMINAL,
    TASK_TRANSITIONS,
    AttemptState,
    InvalidTransition,
    TaskState,
    Wheel,
    validate_attempt_transition,
    validate_task_transition,
    wheel_after_task_state,
)


def test_task_terminal_states_have_no_exits():
    for s in TASK_TERMINAL:
        assert TASK_TRANSITIONS[s] == frozenset()
    for s in ATTEMPT_TERMINAL:
        assert ATTEMPT_TRANSITIONS[s] == frozenset()


@pytest.mark.parametrize(
    "cur,tgt",
    [
        ("RECEIVED", "VALIDATING"),
        ("VALIDATING", "QUEUED"),
        ("VALIDATING", "REJECTED"),
        ("QUEUED", "RUNNING"),
        ("RUNNING", "SUCCEEDED"),
        ("RUNNING", "QUEUED"),
        ("WAITING", "QUEUED"),
        ("WAITING", "VALIDATING"),
        ("RECEIVED", "CANCELLED"),
    ],
)
def test_task_allowed(cur, tgt):
    validate_task_transition(cur, tgt)


@pytest.mark.parametrize(
    "cur,tgt",
    [
        ("RECEIVED", "RUNNING"),
        ("RECEIVED", "SUCCEEDED"),
        ("QUEUED", "SUCCEEDED"),
        ("SUCCEEDED", "RUNNING"),
        ("FAILED", "QUEUED"),
        ("CANCELLED", "RUNNING"),
        ("VALIDATING", "RUNNING"),
    ],
)
def test_task_forbidden(cur, tgt):
    with pytest.raises(InvalidTransition):
        validate_task_transition(cur, tgt)


def test_waiting_requires_a_named_reason():
    with pytest.raises(InvalidTransition):
        validate_task_transition("RUNNING", "WAITING")
    validate_task_transition("RUNNING", "WAITING", waiting_reason="ENVIRONMENT")
    with pytest.raises(ValueError):
        validate_task_transition(
            "RUNNING", "WAITING", waiting_reason="DEVICE"
        )  # 旧词已退役


def test_same_state_is_not_a_free_pass():
    with pytest.raises(InvalidTransition):
        validate_task_transition("RUNNING", "RUNNING")


@pytest.mark.parametrize(
    "cur,tgt",
    [
        ("CREATED", "RUNNING"),
        ("RUNNING", "SUSPENDED"),
        ("SUSPENDED", "RUNNING"),
        ("SUSPENDED", "ABANDONED"),
        ("RUNNING", "BUDGET_EXCEEDED"),
        ("RUNNING", "COMPLETED"),
    ],
)
def test_attempt_allowed(cur, tgt):
    validate_attempt_transition(cur, tgt)


@pytest.mark.parametrize(
    "cur,tgt",
    [
        ("COMPLETED", "RUNNING"),
        ("CREATED", "COMPLETED"),
        ("ABANDONED", "RUNNING"),
        ("BUDGET_EXCEEDED", "RUNNING"),
    ],
)
def test_attempt_forbidden(cur, tgt):
    with pytest.raises(InvalidTransition):
        validate_attempt_transition(cur, tgt)


def test_wheel_returns_to_user_only_on_terminal():
    for s in TaskState:
        expected = Wheel.user if s in TASK_TERMINAL else None
        assert wheel_after_task_state(s) == expected
    assert AttemptState.SUSPENDED not in ATTEMPT_TERMINAL
