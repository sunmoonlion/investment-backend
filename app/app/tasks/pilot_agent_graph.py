from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from langgraph.types import Command

from app.application.agent.pilot_redis import pilot_run_events_channel
from app.domain.agent.knowledge import (
    Citation,
    KnowledgeQuery,
    RetrievalSecurityContext,
)
from app.infrastructure.agent.pilot_repository import PilotRepository
from app.infrastructure.external.knowledge_retrieval import (
    get_knowledge_retrieval_client,
)
from app.infrastructure.external.pilot_llm import OpenAICompatiblePilotLLM
from app.infrastructure.graph.checkpointer import phase0_postgres_checkpointer
from app.infrastructure.graph.langgraph_runtime import LangGraphRuntimeService
from app.infrastructure.graph.pilot_graph import (
    approval_action_id,
    build_pilot_graph,
)
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis
from app.worker import celery_app
from core.config import get_settings

logger = logging.getLogger(__name__)
_worker_loop: asyncio.AbstractEventLoop | None = None


def _run_in_worker_loop(coro: Any) -> Any:
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coro)


async def _publish(run_id: uuid.UUID, event: dict[str, Any]) -> None:
    await get_redis().client.publish(
        pilot_run_events_channel(str(run_id)),
        json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str),
    )


async def _append(
    repository: PilotRepository,
    *,
    run_id: uuid.UUID,
    session_id: uuid.UUID,
    event_type: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    event = await repository.append_browser_event(
        run_id=run_id,
        session_id=session_id,
        event_type=event_type,
        data=data,
    )
    await _publish(run_id, event)
    return event


async def _cancelled(repository: PilotRepository, run_id: uuid.UUID) -> bool:
    current = await repository.get_run_for_worker(run_id)
    return current is None or current["status"] == "cancelled" or bool(
        current["cancel_requested"]
    )


async def _run_pilot_graph(run_id_value: str, resume: str | None = None) -> None:
    settings = get_settings()
    settings.require_agent_pilot()
    run_id = uuid.UUID(run_id_value)
    await get_postgres().init()
    await get_redis().init()

    async with get_postgres().session_factory() as session:
        repository = PilotRepository(session)
        run = await repository.get_run_for_worker(run_id)
        if run is None:
            raise RuntimeError("pilot run not found")
        if run["status"] in {"completed", "failed", "cancelled"}:
            return
        session_id = uuid.UUID(str(run["session_id"]))
        owner_actor_id = uuid.UUID(str(run["owner_actor_id"]))

        try:
            await repository.set_status(
                run_id=run_id,
                session_id=session_id,
                status="running",
            )
            await _append(
                repository,
                run_id=run_id,
                session_id=session_id,
                event_type="status",
                data={"status": "running"},
            )

            with phase0_postgres_checkpointer() as checkpointer:
                graph = build_pilot_graph(checkpointer=checkpointer)
                config = {"configurable": {"thread_id": str(session_id)}}
                runtime = LangGraphRuntimeService()

                if resume is None:
                    retrieval = await get_knowledge_retrieval_client().retrieve(
                        KnowledgeQuery(
                            request_id=uuid.uuid4(),
                            query=str(run["user_input"]),
                            dataset_keys=list(settings.agent_pilot_dataset_key_list),
                            top_k=5,
                            token_budget=4000,
                            security_context=RetrievalSecurityContext(
                                tenant_id="sunmoonai",
                                actor_id=owner_actor_id,
                                actor_type="human",
                                policy_version=settings.auth_policy_version,
                                delegated_run_id=run_id,
                            ),
                        )
                    )
                    if not retrieval.evidence:
                        raise RuntimeError(
                            "pilot retrieval returned no authorized evidence"
                        )
                    citations = [
                        Citation.from_evidence(item) for item in retrieval.evidence
                    ]
                    for citation in citations:
                        await _append(
                            repository,
                            run_id=run_id,
                            session_id=session_id,
                            event_type="citation",
                            data={
                                "citation": citation.model_dump(mode="json")
                            },
                        )
                    if await _cancelled(repository, run_id):
                        return
                    draft = await OpenAICompatiblePilotLLM(
                        base_url=settings.agent_pilot_llm_base_url,
                        api_key=settings.agent_pilot_llm_api_key or "",
                        model=settings.agent_pilot_llm_model,
                        timeout_seconds=settings.agent_pilot_llm_timeout_seconds,
                    ).answer(
                        user_input=str(run["user_input"]),
                        evidence=retrieval.evidence,
                    )
                    if await _cancelled(repository, run_id):
                        return
                    result = runtime.stream_with_config(
                        graph,
                        {
                            "run_id": str(run_id),
                            "user_input": str(run["user_input"]),
                            "draft": draft,
                            "citations": [
                                item.model_dump(mode="json") for item in citations
                            ],
                        },
                        config,
                    )
                    if not result.interrupted:
                        raise RuntimeError(
                            "pilot graph did not request required approval"
                        )
                    action_id = approval_action_id(str(run_id))
                    await _append(
                        repository,
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
                    await repository.set_status(
                        run_id=run_id,
                        session_id=session_id,
                        status="waiting",
                        resume_token=action_id,
                    )
                    await _append(
                        repository,
                        run_id=run_id,
                        session_id=session_id,
                        event_type="status",
                        data={"status": "waiting_for_input"},
                    )
                    return

                result = runtime.stream_with_config(
                    graph,
                    Command(resume=resume),
                    config,
                )
                summary = result.state.get("summary")
                if not isinstance(summary, str) or not summary:
                    raise RuntimeError(
                        "pilot graph completed without a grounded summary"
                    )
                if await _cancelled(repository, run_id):
                    return
                await _append(
                    repository,
                    run_id=run_id,
                    session_id=session_id,
                    event_type="delta",
                    data={"text": summary},
                )
                await repository.set_status(
                    run_id=run_id,
                    session_id=session_id,
                    status="completed",
                )
                await _append(
                    repository,
                    run_id=run_id,
                    session_id=session_id,
                    event_type="completed",
                    data={"summary": summary},
                )
        except Exception as exc:
            logger.exception(
                "pilot graph failed run_id=%s error_type=%s",
                run_id,
                type(exc).__name__,
            )
            current = await repository.get_run_for_worker(run_id)
            if current is not None and current["status"] not in {
                "completed",
                "failed",
                "cancelled",
            }:
                await repository.set_status(
                    run_id=run_id,
                    session_id=session_id,
                    status="failed",
                    error=type(exc).__name__,
                )
                await _append(
                    repository,
                    run_id=run_id,
                    session_id=session_id,
                    event_type="failed",
                    data={
                        "code": "pilot_failed",
                        "message": "The pilot run failed safely.",
                    },
                )
            raise


@celery_app.task(name="app.tasks.pilot_agent_graph.run")
def run_pilot_agent_graph(run_id: str, resume: str | None = None) -> None:
    _run_in_worker_loop(_run_pilot_graph(run_id, resume))
