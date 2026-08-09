from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.types import Command

from app.application.agent.event_sink import DBEventSink
from app.application.agent.run_logging import lineage_log_extra
from app.application.agent.side_effect_service import ToolSideEffectService
from app.domain.agent.models import DomainEvent, RunLineage
from app.domain.agent.security import SecurityContext
from app.infrastructure.agent.repositories import AgentRepository
from app.infrastructure.agent.session_lock import RedisSessionLock
from app.infrastructure.graph.checkpointer import phase0_postgres_checkpointer
from app.infrastructure.graph.langgraph_runtime import LangGraphRuntimeService
from app.infrastructure.graph.walking_skeleton import build_walking_skeleton_graph
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis
from app.worker import celery_app
from core.config import get_settings

logger = logging.getLogger(__name__)
_worker_loop: asyncio.AbstractEventLoop | None = None


def _stream_graph(
    graph: Any, graph_input: Any, config: dict[str, Any]
) -> dict[str, Any]:
    return (
        LangGraphRuntimeService().stream_with_config(graph, graph_input, config).state
    )


def _run_in_worker_loop(coro: Any) -> Any:
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coro)


async def _run_agent_graph(
    run_id: str,
    user_input: str | None = None,
    security_context: dict | None = None,
) -> None:
    await get_postgres().init()
    await get_redis().init()
    effective_security = SecurityContext.model_validate(
        security_context or SecurityContext.single_tenant().model_dump()
    )
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
        logger.info("agent graph run loaded", extra=lineage_log_extra(lineage))
        sink = DBEventSink(repository, get_redis().client)
        lock = RedisSessionLock(
            get_redis().client,
            ttl_seconds=get_settings().agent_session_lock_ttl_seconds,
        )
        lock_token = await lock.acquire(session_id=session_id, owner=run_id)
        if not lock_token:
            error = f"session is already locked: {session_id}"
            logger.warning(
                "agent graph session lock busy",
                extra=lineage_log_extra(lineage, error_code="session_locked"),
            )
            await sink.append(
                DomainEvent(
                    type="RunFailed",
                    payload={"error": error, "code": "session_locked"},
                    lineage=lineage,
                )
            )
            await repository.set_run_status(
                run_id=run_id,
                session_id=session_id,
                status="failed",
                error=error,
            )
            return

        try:
            logger.info("agent graph run started", extra=lineage_log_extra(lineage))
            await repository.set_run_status(
                run_id=run_id, session_id=session_id, status="running"
            )
            if user_input is None:
                await sink.append(
                    DomainEvent(
                        type="RunStarted",
                        payload={"security_context": effective_security.model_dump()},
                        lineage=lineage,
                    )
                )

            config = {"configurable": {"thread_id": session_id}}
            if not await lock.renew(lock_token):
                raise RuntimeError("session lock was lost before graph execution")
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
                            payload={
                                "text": user_input,
                                "security_context": effective_security.model_dump(),
                            },
                            lineage=lineage,
                        )
                    )
                    result = _stream_graph(graph, Command(resume=user_input), config)
            if not await lock.renew(lock_token):
                raise RuntimeError("session lock was lost after graph execution")

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
                logger.info(
                    "agent graph run waiting for input",
                    extra=lineage_log_extra(lineage),
                )
                return

            await sink.append(
                DomainEvent(
                    type="ToolCallStarted",
                    payload={"security_context": effective_security.model_dump()},
                    lineage=lineage,
                )
            )
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
            logger.info("agent graph run completed", extra=lineage_log_extra(lineage))
        except Exception as exc:
            logger.exception(
                "agent graph failed",
                extra=lineage_log_extra(lineage, error=str(exc)),
            )
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
        finally:
            await lock.release(lock_token)


@celery_app.task(name="app.tasks.agent_graph.run")
def run_agent_graph(
    run_id: str,
    user_input: str | None = None,
    security_context: dict | None = None,
) -> None:
    _run_in_worker_loop(_run_agent_graph(run_id, user_input, security_context))
