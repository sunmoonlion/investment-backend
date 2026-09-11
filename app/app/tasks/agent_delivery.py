from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import suppress

from app.application.agent.execution_service import AgentExecutionService
from app.domain.agent.knowledge import (
    Citation,
    KnowledgeQuery,
    RetrievalSecurityContext,
)
from app.infrastructure.agent.delivery import AgentDelivery
from app.infrastructure.agent.pilot_repository import PilotRepository
from app.infrastructure.agent.repositories import AgentRepository
from app.infrastructure.agent.transactions import LeaseLost
from app.infrastructure.agent.worker_loop import (
    run_in_worker_loop as _run_in_worker_loop,
)
from app.infrastructure.graph.checkpointer import phase0_postgres_checkpointer
from app.infrastructure.graph.executor import GraphExecutor
from app.infrastructure.graph.pilot_graph import build_pilot_graph
from app.infrastructure.graph.walking_skeleton import build_walking_skeleton_graph
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis
from app.worker import celery_app
from core.config import get_settings


async def prepare_pilot_input(run):
    from app.infrastructure.external.knowledge_retrieval import (
        get_knowledge_retrieval_client,
    )
    from app.infrastructure.external.pilot_llm import OpenAICompatiblePilotLLM

    settings = get_settings()
    settings.require_agent_pilot()
    evidence = await get_knowledge_retrieval_client().retrieve(
        KnowledgeQuery(
            request_id=uuid.uuid4(),
            query=str(run["user_input"]),
            dataset_keys=list(settings.agent_pilot_dataset_key_list),
            top_k=5,
            token_budget=4000,
            security_context=RetrievalSecurityContext(
                tenant_id="sunmoonai",
                actor_id=uuid.UUID(str(run["owner_actor_id"])),
                actor_type="human",
                policy_version=settings.agent_pilot_internal_auth_policy_version,
                delegated_run_id=uuid.UUID(str(run["id"])),
            ),
        )
    )
    if not evidence.evidence:
        raise RuntimeError("pilot retrieval returned no authorized evidence")
    draft = await OpenAICompatiblePilotLLM(
        base_url=settings.agent_pilot_llm_base_url,
        api_key=settings.agent_pilot_llm_api_key or "",
        model=settings.agent_pilot_llm_model,
        timeout_seconds=settings.agent_pilot_llm_timeout_seconds,
    ).answer(user_input=str(run["user_input"]), evidence=evidence.evidence)
    return {
        "run_id": str(run["id"]),
        "user_input": str(run["user_input"]),
        "draft": draft,
        "citations": [
            Citation.from_evidence(item).model_dump(mode="json")
            for item in evidence.evidence
        ],
    }


async def execute_command(
    command_id: str,
    *,
    sessions=None,
    executor=None,
    prepare_input=None,
    lease_seconds: int = 60,
):
    if sessions is None:
        await get_postgres().init()
        sessions = get_postgres().session_factory
    delivery = AgentDelivery(sessions, lease_seconds=lease_seconds)
    claimed = await delivery.claim_execution(uuid.UUID(command_id))
    if claimed is None:
        return
    lease, payload = claimed
    pilot = payload["kind"] == "pilot"
    if executor is None:
        executor = GraphExecutor(
            build_pilot_graph if pilot else build_walking_skeleton_graph,
            provider="pilot-v1" if pilot else "phase0-v1",
            resume_field="approval" if pilot else "user_input",
            checkpointer_factory=phase0_postgres_checkpointer,
        )

    async def heartbeat():
        while True:
            await asyncio.sleep(lease_seconds / 3)
            await delivery.renew(lease)

    async def work():
        async with sessions() as session:
            repo = (PilotRepository if pilot else AgentRepository)(session, lease=lease)
            await AgentExecutionService(repo, executor).execute(
                payload=payload,
                lease=lease,
                prepare_input=(prepare_input or prepare_pilot_input) if pilot else None,
            )

    job = asyncio.create_task(work())
    renewal = asyncio.create_task(heartbeat())
    try:
        done, _ = await asyncio.wait(
            {job, renewal}, return_when=asyncio.FIRST_COMPLETED
        )
        if renewal in done:
            await renewal  # propagate lease/DB failure before accepting more results
        await job
    finally:
        renewal.cancel()
        job.cancel()
        with suppress(asyncio.CancelledError, LeaseLost):
            await renewal
        with suppress(asyncio.CancelledError, LeaseLost):
            await job
        await delivery.release(lease)


async def pump(*, sessions=None, publish=None, limit: int = 100):
    # Resolve after Celery task registration; either transport can import first.
    from app.tasks.durable_delivery import pump as pump_delivery

    if sessions is None:
        await get_postgres().init()
        sessions = get_postgres().session_factory
    delivery = AgentDelivery(sessions)

    async def publish_message(message):
        if publish is not None:
            await publish(message)
        elif message["topic"] == "agent.execution":
            # This adapter supplies transport, not its own retry/dead-letter loop.
            await asyncio.to_thread(
                run_agent_delivery.apply_async,
                args=[str(message["id"])],
                task_id=str(message["id"]),
            )
        else:
            await get_redis().init()
            await get_redis().client.publish(
                message["payload"]["channel"],
                json.dumps(message["payload"]["message"], ensure_ascii=False),
            )

    return await pump_delivery(delivery, publish_message, limit=limit)


async def _with_worker_resources(coro):
    # Celery can alternate domain and generic tasks in the same process. Never
    # leave loop-bound singleton pools for another task's event loop to reuse.
    try:
        return await coro
    finally:
        try:
            await get_redis().shutdown()
        finally:
            await get_postgres().shutdown()


@celery_app.task(
    name="app.tasks.agent_delivery.run", acks_late=True, reject_on_worker_lost=True
)
def run_agent_delivery(command_id: str):
    _run_in_worker_loop(_with_worker_resources(execute_command(command_id)))


@celery_app.task(
    name="app.tasks.agent_delivery.pump", acks_late=True, reject_on_worker_lost=True
)
def pump_agent_delivery():
    _run_in_worker_loop(_with_worker_resources(pump()))
