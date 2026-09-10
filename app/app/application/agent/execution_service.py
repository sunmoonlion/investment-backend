"""Execution policy consumes the session-level port and commits accepted results."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from app.application.agent.event_sink import DBEventSink
from app.domain.agent.executor import (
    AgentExecutorPort,
    ExecutionBinding,
    ExecutionRequest,
)
from app.domain.agent.models import DomainEvent, RunLineage
from app.infrastructure.agent.delivery import TERMINAL
from app.infrastructure.agent.transactions import LeaseLost


class AgentExecutionService:
    def __init__(self, repository, executor: AgentExecutorPort):
        self.repository = repository
        self.executor = executor

    async def execute(
        self, *, payload: dict[str, Any], lease, prepare_input=None
    ) -> None:
        repo = self.repository
        pilot = payload["kind"] == "pilot"
        run_id = str(payload["run_id"])
        session_id = str(lease.session_id)
        lineage = RunLineage(session_id=session_id, run_id=run_id, root_run_id=run_id)
        binding = None
        async with repo.transaction():
            run = (
                await repo.get_run_for_worker(UUID(run_id))
                if pilot
                else await repo.get_run(run_id)
            )
            if run is None:
                raise ValueError("run not found")
            if run["status"] in TERMINAL:
                await repo.acknowledge(lease.command_id)
                return
            if payload["operation"] == "start" and run["status"] == "waiting":
                # An old start delivery cannot resume or overwrite a waiting turn.
                await repo.acknowledge(lease.command_id)
                return
            if pilot:
                await repo.set_status(
                    run_id=UUID(run_id), session_id=lease.session_id, status="running"
                )
                await repo.append_browser_event(
                    run_id=UUID(run_id),
                    session_id=lease.session_id,
                    event_type="status",
                    data={"status": "running"},
                )
            else:
                await repo.set_run_status(
                    run_id=run_id, session_id=session_id, status="running"
                )
                await DBEventSink(repo).append(
                    DomainEvent(
                        type="RunStarted",
                        payload={
                            "security_context": payload.get("security_context", {})
                        },
                        lineage=lineage,
                    )
                )
        try:
            execution_id = f"{run_id}:{lease.command_id}:{lease.epoch}"
            if payload["operation"] == "resume":
                previous = ExecutionBinding.model_validate(run["execution_state"])
                binding = await self.executor.resume(
                    previous, payload["input"], execution_id=execution_id
                )
            else:
                inputs = (
                    await prepare_input(run)
                    if prepare_input
                    else {
                        "run_id": run_id,
                        "session_id": session_id,
                    }
                )
                # Preserve the walking skeleton's initial-input semantics: input
                # belongs to start, never a resume Command on an empty checkpoint.
                if not pilot and payload.get("input"):
                    inputs["user_input"] = payload["input"]
                async with repo.transaction():
                    await repo.assert_lease()
                binding = await self.executor.start(
                    ExecutionRequest(execution_id=execution_id, input=inputs)
                )
            accepted = False
            async for event in self.executor.events(binding):
                result = event.binding
                if (
                    result.execution_id != binding.execution_id
                    or result.provider != binding.provider
                ):
                    raise ValueError("executor returned a snapshot for another binding")
                async with repo.transaction():
                    await repo.save_execution(run_id, result.model_dump(mode="json"))
                    if result.status == "cancelled":
                        raise LeaseLost("execution was cancelled")
                    if pilot:
                        await self._accept_pilot(run_id, lease.session_id, result)
                    else:
                        await self._accept_phase0(run_id, session_id, lineage, result)
                    await repo.acknowledge(lease.command_id)
                accepted = True
                break
            if not accepted:
                raise ValueError("executor ended without a result")
        except asyncio.CancelledError:
            if binding:
                await self.executor.cancel(binding)
            raise
        except LeaseLost:
            if binding:
                await self.executor.cancel(binding)
            raise
        except Exception as exc:
            # Failure status, event and command acknowledgement are one fenced
            # transaction. A stale worker cannot fail a replacement's run.
            async with repo.transaction():
                if pilot:
                    await repo.set_status(
                        run_id=UUID(run_id),
                        session_id=lease.session_id,
                        status="failed",
                        error=type(exc).__name__,
                    )
                    await repo.append_browser_event(
                        run_id=UUID(run_id),
                        session_id=lease.session_id,
                        event_type="failed",
                        data={
                            "code": "execution_failed",
                            "message": "The run failed safely.",
                        },
                    )
                else:
                    await repo.set_run_status(
                        run_id=run_id,
                        session_id=session_id,
                        status="failed",
                        error=type(exc).__name__,
                    )
                    await DBEventSink(repo).append(
                        DomainEvent(
                            type="RunFailed",
                            payload={"error": type(exc).__name__},
                            lineage=lineage,
                        )
                    )
                await repo.acknowledge(lease.command_id)
            raise
        finally:
            if binding:
                await self.executor.close(binding)

    async def _accept_phase0(self, run_id, session_id, lineage, result):
        repo = self.repository
        sink = DBEventSink(repo)
        if result.status == "waiting":
            import uuid

            token = str(uuid.uuid4())
            await repo.set_run_status(
                run_id=run_id,
                session_id=session_id,
                status="waiting",
                resume_token=token,
            )
            await sink.append(
                DomainEvent(
                    type="HumanInputRequested",
                    payload={"resume_token": token},
                    lineage=lineage,
                )
            )
        elif result.status == "completed":
            await sink.append(DomainEvent(type="ToolCallStarted", lineage=lineage))
            # This skeleton effect is a local DB record, performed in this same
            # fenced transaction. Remote effects must use DurableSideEffectService.
            inserted = await repo.record_side_effect_once(
                tool_call_id=f"phase0:{run_id}:side_effect",
                run_id=run_id,
                result={"message": "phase0 side effect"},
            )
            await sink.append(
                DomainEvent(
                    type="ToolCallCompleted",
                    payload={"inserted": inserted},
                    lineage=lineage,
                )
            )
            await repo.set_run_status(
                run_id=run_id, session_id=session_id, status="completed"
            )
            await sink.append(DomainEvent(type="RunCompleted", lineage=lineage))
        else:
            raise ValueError("executor yielded no final or waiting snapshot")

    async def _accept_pilot(self, run_id, session_id, result):
        from app.infrastructure.graph.pilot_graph import approval_action_id

        repo = self.repository
        run_id = UUID(run_id)
        if result.status == "waiting":
            for citation in result.state.get("citations", []):
                await repo.append_browser_event(
                    run_id=run_id,
                    session_id=session_id,
                    event_type="citation",
                    data={"citation": citation},
                )
            action_id = approval_action_id(str(run_id))
            await repo.set_status(
                run_id=run_id,
                session_id=session_id,
                status="waiting",
                resume_token=action_id,
            )
            await repo.append_browser_event(
                run_id=run_id,
                session_id=session_id,
                event_type="input_required",
                data={
                    "action": {
                        "action_id": action_id,
                        "kind": "confirmation",
                        "prompt": "确认使用这些检索证据生成最终回答？",
                    }
                },
            )
            await repo.append_browser_event(
                run_id=run_id,
                session_id=session_id,
                event_type="status",
                data={"status": "waiting_for_input"},
            )
        elif result.status == "completed":
            summary = result.state.get("summary")
            if not isinstance(summary, str) or not summary:
                raise ValueError("executor returned no summary")
            await repo.set_status(
                run_id=run_id, session_id=session_id, status="completed"
            )
            await repo.append_browser_event(
                run_id=run_id,
                session_id=session_id,
                event_type="delta",
                data={"text": summary},
            )
            await repo.append_browser_event(
                run_id=run_id,
                session_id=session_id,
                event_type="completed",
                data={"summary": summary},
            )
        else:
            raise ValueError("executor yielded no final or waiting snapshot")
