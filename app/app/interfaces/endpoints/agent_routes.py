from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.agent.redis_keys import (
    session_deltas_channel,
    session_events_channel,
)
from app.application.agent.run_service import AgentRunService
from app.domain.agent.commands import CreateRunCommand, ResumeRunCommand
from app.domain.agent.models import UserInput
from app.domain.agent.security import SecurityContext
from app.domain.security import Principal
from app.infrastructure.agent.repositories import AgentRepository
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.postgres import get_db_session
from app.infrastructure.storage.redis import get_redis
from core.config import get_settings

from app.interfaces.middleware.auth import require_research_admin


def require_agent_v4_traffic_enabled() -> None:
    if not get_settings().agent_v4_traffic_enabled:
        raise HTTPException(status_code=404, detail="Agent v4 traffic is disabled")


router = APIRouter(
    prefix="/agent",
    tags=["Agent"],
    dependencies=[Depends(require_agent_v4_traffic_enabled)],
)


class CreateSessionResponse(BaseModel):
    session_id: str
    status: str = "created"


class CreateRunRequest(BaseModel):
    idempotency_key: str | None = None
    agent_profile_key: str | None = None
    user_input: UserInput = UserInput()


class ResumeRunRequest(BaseModel):
    resume_token: str
    user_input: UserInput
    idempotency_key: str | None = None


def get_service(session: AsyncSession) -> AgentRunService:
    return AgentRunService(AgentRepository(session))


@router.post("/sessions", response_model=CreateSessionResponse)
async def create_session(
    principal: Principal = Depends(require_research_admin),
    session: AsyncSession = Depends(get_db_session),
):
    service = get_service(session)
    return CreateSessionResponse(
        session_id=await service.create_session(owner_actor_id=str(principal.actor_id))
    )


def security_context_for(principal: Principal) -> SecurityContext:
    return SecurityContext(
        actor_id=str(principal.actor_id),
        roles=list(principal.roles) or ["agent_user"],
        permissions=sorted(principal.scopes),
    )


@router.post("/sessions/{session_id}/runs")
async def create_run(
    session_id: str,
    request: CreateRunRequest,
    principal: Principal = Depends(require_research_admin),
    session: AsyncSession = Depends(get_db_session),
):
    service = get_service(session)
    command = CreateRunCommand(
        session_id=session_id,
        owner_actor_id=str(principal.actor_id),
        user_input=request.user_input,
        idempotency_key=request.idempotency_key,
        agent_profile_key=request.agent_profile_key,
        security_context=security_context_for(principal),
    )
    try:
        return await service.create_run(command)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="agent session access denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    request: ResumeRunRequest,
    principal: Principal = Depends(require_research_admin),
    session: AsyncSession = Depends(get_db_session),
):
    service = get_service(session)
    try:
        return await service.resume_run(
            ResumeRunCommand(
                run_id=run_id,
                owner_actor_id=str(principal.actor_id),
                resume_token=request.resume_token,
                user_input=request.user_input,
                idempotency_key=request.idempotency_key,
                security_context=security_context_for(principal),
            )
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="agent run access denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/events")
async def list_events(
    session_id: str,
    after_event_id: str | None = None,
    principal: Principal = Depends(require_research_admin),
    session: AsyncSession = Depends(get_db_session),
):
    repository = AgentRepository(session)
    try:
        await repository.assert_session_owner(
            session_id=session_id,
            owner_actor_id=str(principal.actor_id),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="agent session access denied") from exc
    return {"events": await repository.list_ui_events(session_id=session_id, after_event_id=after_event_id)}


@router.get("/sessions/{session_id}/stream")
async def stream_events(
    session_id: str,
    last_event_id: str | None = None,
    principal: Principal = Depends(require_research_admin),
    session: AsyncSession = Depends(get_db_session),
):
    repository = AgentRepository(session)
    try:
        await repository.assert_session_owner(
            session_id=session_id,
            owner_actor_id=str(principal.actor_id),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="agent session access denied") from exc

    def to_sse(payload: dict) -> str:
        data = json.dumps(payload, ensure_ascii=False, default=str)
        return f"id: {payload.get('id') or ''}\nevent: {payload.get('type', 'message')}\ndata: {data}\n\n"

    async def event_generator():
        redis = get_redis().client
        pubsub = redis.pubsub()
        channels = [
            session_events_channel(session_id),
            session_deltas_channel(session_id),
        ]
        # Subscribe before reading the durable snapshot.  Otherwise an event
        # committed between the DB query and Redis subscribe is lost forever.
        await pubsub.subscribe(*channels)
        replayed_event_ids: set[str] = set()
        try:
            async with get_postgres().session_factory() as session:
                repository = AgentRepository(session)
                for event in await repository.list_ui_events(
                    session_id=session_id,
                    after_event_id=last_event_id,
                ):
                    event_id = str(event.get("id") or "")
                    if event_id:
                        replayed_event_ids.add(event_id)
                    yield to_sse(event)

            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                payload = message["data"]
                data = json.loads(payload)
                channel = message.get("channel")
                if isinstance(channel, bytes):
                    channel = channel.decode()
                # The durable UI-event snapshot and the live event channel can
                # overlap.  Deduplicate only UI events; LiveDelta carries the
                # same final_event_id but is a distinct client signal.  Do not
                # retain every later live ID: Redis Pub/Sub is at-most-once and
                # an ever-growing set would leak memory on a long-lived stream.
                if channel == session_events_channel(session_id):
                    event_id = str(data.get("id") or "")
                    if event_id and event_id in replayed_event_ids:
                        continue
                yield to_sse(data)
        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.aclose()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
