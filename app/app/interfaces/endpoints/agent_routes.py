from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.agent.run_service import AgentRunService
from app.domain.agent.commands import CreateRunCommand, ResumeRunCommand
from app.domain.agent.models import UserInput
from app.infrastructure.agent.repositories import AgentRepository
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.postgres import get_db_session
from app.infrastructure.storage.redis import get_redis

router = APIRouter(prefix="/agent", tags=["Agent"])


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
async def create_session(session: AsyncSession = Depends(get_db_session)):
    service = get_service(session)
    return CreateSessionResponse(session_id=await service.create_session())


@router.post("/sessions/{session_id}/runs")
async def create_run(
    session_id: str,
    request: CreateRunRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = get_service(session)
    command = CreateRunCommand(
        session_id=session_id,
        user_input=request.user_input,
        idempotency_key=request.idempotency_key,
        agent_profile_key=request.agent_profile_key,
    )
    return await service.create_run(command)


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    request: ResumeRunRequest,
    session: AsyncSession = Depends(get_db_session),
):
    service = get_service(session)
    try:
        return await service.resume_run(
            ResumeRunCommand(
                run_id=run_id,
                resume_token=request.resume_token,
                user_input=request.user_input,
                idempotency_key=request.idempotency_key,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sessions/{session_id}/events")
async def list_events(
    session_id: str,
    after_event_id: str | None = None,
    session: AsyncSession = Depends(get_db_session),
):
    repository = AgentRepository(session)
    return {"events": await repository.list_ui_events(session_id=session_id, after_event_id=after_event_id)}


@router.get("/sessions/{session_id}/stream")
async def stream_events(session_id: str, last_event_id: str | None = None):
    def to_sse(payload: dict) -> str:
        data = json.dumps(payload, ensure_ascii=False, default=str)
        return f"id: {payload.get('id') or ''}\nevent: {payload.get('type', 'message')}\ndata: {data}\n\n"

    async def event_generator():
        async with get_postgres().session_factory() as session:
            repository = AgentRepository(session)
            for event in await repository.list_ui_events(
                session_id=session_id,
                after_event_id=last_event_id,
            ):
                yield to_sse(event)

        redis = get_redis().client
        pubsub = redis.pubsub()
        await pubsub.subscribe(f"agent:session:{session_id}:events")
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                payload = message["data"]
                data = json.loads(payload)
                yield to_sse(data)
        finally:
            await pubsub.unsubscribe(f"agent:session:{session_id}:events")
            await pubsub.aclose()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
