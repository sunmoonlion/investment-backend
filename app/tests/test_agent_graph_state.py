from __future__ import annotations

import pytest

from app.infrastructure.graph.state import (
    BaseAgentState,
    append_unique_by_id,
    merge_versioned_dict,
    validate_base_state_layering,
)


def test_versioned_reducer_rejects_stale_plan_overwrite() -> None:
    old = {"id": "plan-1", "version": 2, "title": "new"}
    stale = {"id": "plan-1", "version": 1, "title": "old"}

    assert merge_versioned_dict(old, stale) == old
    assert merge_versioned_dict(old, {"id": "plan-1", "version": 3}) == {
        "id": "plan-1",
        "version": 3,
    }


def test_append_unique_reducer_is_idempotent_under_replay() -> None:
    old = [{"id": "tool-1", "result": "ok"}]
    replay = [{"id": "tool-1", "result": "ok"}, {"id": "tool-2", "result": "ok"}]

    assert append_unique_by_id(old, replay) == [
        {"id": "tool-1", "result": "ok"},
        {"id": "tool-2", "result": "ok"},
    ]


def test_base_agent_state_allows_refs_but_rejects_cross_layer_payloads() -> None:
    state: BaseAgentState = {
        "session_id": "session-1",
        "run_id": "run-1",
        "artifacts": [{"id": "artifact-1", "uri": "s3://bucket/key", "hash": "sha256:abc"}],
    }

    validate_base_state_layering(state)

    with pytest.raises(ValueError, match="object bodies"):
        validate_base_state_layering(
            {
                "session_id": "session-1",
                "run_id": "run-1",
                "artifacts": [{"id": "artifact-1", "uri": "s3://bucket/key", "body": "..."}],
            }
        )

    with pytest.raises(ValueError, match="cross-layer keys"):
        validate_base_state_layering(
            {
                "session_id": "session-1",
                "run_id": "run-1",
                "event_history": [],
            }
        )
