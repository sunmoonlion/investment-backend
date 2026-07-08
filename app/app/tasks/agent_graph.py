from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.types import Command

from app.application.agent.event_sink import DBEventSink
from app.application.agent.side_effect_service import ToolSideEffectService
from app.domain.agent.models import DomainEvent, RunLineage
from app.infrastructure.agent.repositories import AgentRepository
from app.infrastructure.graph.checkpointer import phase0_postgres_checkpointer
from app.infrastructure.graph.langgraph_runtime import LangGraphRuntimeService
from app.infrastructure.graph.walking_skeleton import build_walking_skeleton_graph
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis
from app.worker import celery_app

logger = logging.getLogger(__name__)


def _stream_graph(graph: Any, graph_input: Any, config: dict[str, Any]) -> dict[str, Any]:
    return LangGraphRuntimeService().stream_with_config(graph, graph_input, config).state


async def _run_agent_graph(run_id: str, user_input: str | None = None) -> None:
    await get_postgres().init()
    await get_redis().init()
    async with get_postgres().session_factory() as session:
        repository = AgentRepository(session)
        run = await repository.get_run(run_id)
        if not run:
            raise RuntimeError(f"run not found: {run_id}")
        if run["status"] == "completed":
            logger.info("agent graph run already completed run_id=%s", run_id)
            return
        session_id = str(run["session_id"])
        lineage = RunLineage(session_id=session_id, run_id=run_id, root_run_id=run_id)
        sink = DBEventSink(repository, get_redis().client)

        await repository.set_run_status(run_id=run_id, session_id=session_id, status="running")
        if user_input is None:
            await sink.append(DomainEvent(type="RunStarted", lineage=lineage))

        config = {"configurable": {"thread_id": session_id}}
        try:
            with phase0_postgres_checkpointer() as checkpointer:
                graph = build_walking_skeleton_graph(checkpointer=checkpointer)
                if user_input is None:
                    result = _stream_graph(
                        graph,
                        {"session_id": session_id, "run_id": run_id},
                        config,
                    )
                else:
                    await sink.append(
                        DomainEvent(
                            type="UserInputReceived",
                            payload={"text": user_input},
                            lineage=lineage,
                        )
                    )
                    result = _stream_graph(graph, Command(resume=user_input), config)

            if "__interrupt__" in result:
                resume_token = f"phase0:{run_id}"
                await sink.append(
                    DomainEvent(
                        type="HumanInputRequested",
                        payload={"resume_token": resume_token},
                        lineage=lineage,
                    )
                )
                await repository.set_run_status(
                    run_id=run_id,
                    session_id=session_id,
                    status="waiting",
                    resume_token=resume_token,
                )
                return

            await sink.append(DomainEvent(type="ToolCallStarted", lineage=lineage))
            inserted = await ToolSideEffectService(repository).record_once(
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
            await sink.append(DomainEvent(type="RunCompleted", lineage=lineage))
            await repository.set_run_status(
                run_id=run_id, session_id=session_id, status="completed"
            )
        except Exception as exc:
            logger.exception("agent graph failed run_id=%s", run_id)
            await sink.append(
                DomainEvent(
                    type="RunFailed",
                    payload={"error": str(exc)},
                    lineage=lineage,
                )
            )
            await repository.set_run_status(
                run_id=run_id, session_id=session_id, status="failed", error=str(exc)
            )
            raise


@celery_app.task(name="app.tasks.agent_graph.run")
def run_agent_graph(run_id: str, user_input: str | None = None) -> None:
    asyncio.run(_run_agent_graph(run_id, user_input))
