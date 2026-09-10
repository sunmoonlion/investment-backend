from __future__ import annotations

import pytest

from app.domain.agent.executor import ExecutionBinding, ExecutionRequest
from app.infrastructure.graph.executor import GraphExecutor
from app.infrastructure.graph.pilot_graph import build_pilot_graph
from app.infrastructure.graph.walking_skeleton import build_walking_skeleton_graph


@pytest.mark.asyncio
async def test_port_resumes_serialized_binding_in_a_fresh_executor():
    first = GraphExecutor(
        build_walking_skeleton_graph, provider="phase0-v1", resume_field="user_input"
    )
    binding = await first.start(
        ExecutionRequest(
            execution_id="attempt-1", input={"session_id": "s", "run_id": "r"}
        )
    )
    events = [event async for event in first.events(binding)]
    waiting = events[-1].binding
    assert waiting.status == "waiting"
    assert (await first.inspect(binding)).status == "waiting"
    assert [event async for event in first.events(binding, after=1)] == []
    await first.close(binding)
    restored = ExecutionBinding.model_validate_json(waiting.model_dump_json())
    second = GraphExecutor(
        build_walking_skeleton_graph, provider="phase0-v1", resume_field="user_input"
    )
    resumed = await second.resume(restored, "yes", execution_id="attempt-2")
    result = [event async for event in second.events(resumed)]
    assert result[-1].binding.status == "completed"
    assert result[-1].binding.state["side_effect_done"] is True
    assert result[-1].binding.execution_id != waiting.execution_id
    await second.close(resumed)


@pytest.mark.asyncio
async def test_pilot_adapter_keeps_approval_across_process_boundary():
    first = GraphExecutor(
        build_pilot_graph, provider="pilot-v1", resume_field="approval"
    )
    binding = await first.start(
        ExecutionRequest(
            execution_id="draft",
            input={
                "run_id": "r",
                "user_input": "question",
                "draft": "answer",
                "citations": [{"id": "e"}],
            },
        )
    )
    waiting = [event.binding async for event in first.events(binding)][-1]
    assert waiting.status == "waiting"
    await first.close(binding)
    second = GraphExecutor(
        build_pilot_graph, provider="pilot-v1", resume_field="approval"
    )
    resumed = await second.resume(waiting, "confirm", execution_id="accepted")
    completed = [event.binding async for event in second.events(resumed)][-1]
    assert completed.state["summary"] == "answer"
    await second.close(resumed)


@pytest.mark.asyncio
async def test_cancel_does_not_accept_a_late_snapshot_and_closes_resources():
    executor = GraphExecutor(
        build_walking_skeleton_graph, provider="phase0-v1", resume_field="user_input"
    )
    binding = await executor.start(
        ExecutionRequest(
            execution_id="cancel-me", input={"run_id": "r", "user_input": "go"}
        )
    )
    assert (await executor.cancel(binding)).status == "cancelled"
    result = [event.binding async for event in executor.events(binding)][-1]
    assert result.status == "cancelled"
    await executor.close(binding)
    assert not executor._tasks
