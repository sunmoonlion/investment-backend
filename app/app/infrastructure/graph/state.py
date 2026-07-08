from __future__ import annotations

from typing import Any, NotRequired, Sequence, TypedDict

from app.domain.agent.models import StoredMessage


class ArtifactRef(TypedDict, total=False):
    id: str
    uri: str
    hash: NotRequired[str]
    media_type: NotRequired[str]
    metadata: NotRequired[dict[str, Any]]


class BaseAgentState(TypedDict, total=False):
    session_id: str
    run_id: str
    tenant_id: str
    user_id: str | None
    project_id: str | None
    user_input: dict[str, Any]
    messages: list[StoredMessage]
    pending_tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    artifacts: list[ArtifactRef]
    recalled_memory_refs: list[str]
    memory_summary_ref: str | None
    status: str
    error: dict[str, Any] | None
    budget: dict[str, Any]
    lineage: dict[str, Any]


class PlannerReactState(BaseAgentState, total=False):
    plan: dict[str, Any] | None
    current_step_id: str | None
    current_step: dict[str, Any] | None


def merge_versioned_dict(
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if old is None:
        return new
    if new is None:
        return old
    old_version = int(old.get("version") or 0)
    new_version = int(new.get("version") or 0)
    return new if new_version >= old_version else old


def append_unique_by_id(
    old: Sequence[dict[str, Any]] | None,
    new: Sequence[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    existing = list(old or [])
    seen = {item.get("id") for item in existing if item.get("id")}
    for item in new or []:
        item_id = item.get("id")
        if item_id and item_id in seen:
            continue
        existing.append(dict(item))
        if item_id:
            seen.add(item_id)
    return existing


FORBIDDEN_STATE_KEYS = {
    "event_history",
    "full_event_history",
    "long_term_memories",
    "memory_dump",
}
FORBIDDEN_ARTIFACT_KEYS = {"body", "bytes", "content", "file_body", "raw"}


def validate_base_state_layering(state: BaseAgentState) -> None:
    forbidden_state_keys = FORBIDDEN_STATE_KEYS.intersection(state.keys())
    if forbidden_state_keys:
        keys = ", ".join(sorted(forbidden_state_keys))
        raise ValueError(f"BaseAgentState contains cross-layer keys: {keys}")

    for artifact in state.get("artifacts", []):
        forbidden_artifact_keys = FORBIDDEN_ARTIFACT_KEYS.intersection(artifact.keys())
        if forbidden_artifact_keys:
            keys = ", ".join(sorted(forbidden_artifact_keys))
            raise ValueError(f"ArtifactRef must store refs only, not object bodies: {keys}")
