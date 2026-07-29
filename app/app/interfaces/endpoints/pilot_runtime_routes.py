from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.agent.pilot_redis import pilot_run_events_channel
from app.application.agent.pilot_service import PilotService
from app.application.errors.exceptions import (
    BadRequestError,
    ForbiddenError,
)
from app.domain.security import Principal
from app.infrastructure.agent.pilot_repository import PilotRepository
from app.infrastructure.security.pilot_service_auth import require_pilot_service
from app.infrastructure.storage.postgres import get_db_session, get_postgres
from app.infrastructure.storage.redis import get_redis
from app.interfaces.schemas.pilot_runtime import (
    PILOT_COMMAND_ADAPTER,
    PilotCancelCommand,
    PilotCreateRun,
    PilotResumeCommand,
    PilotRunCommand,
    PilotRunSnapshot,
    SourceResolution,
)

router = APIRouter(
    prefix="/internal/v1/research",
    tags=["Internal Research Runtime Pilot"],
)


def service(session: AsyncSession) -> PilotService:
    return PilotService(PilotRepository(session))


def delegated_actor(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise BadRequestError(
            "The delegated actor identifier is invalid"
        ) from exc


@router.post("/runs", response_model=PilotRunSnapshot)
async def create_run(
    command: PilotCreateRun,
    _: Principal = Depends(require_pilot_service),
    session: AsyncSession = Depends(get_db_session),
) -> PilotRunSnapshot:
    return await service(session).create_run(command)


@router.get("/runs/{run_id}", response_model=PilotRunSnapshot)
async def get_run(
    run_id: UUID,
    delegated_actor_id: Annotated[str, Header(alias="X-Delegated-Actor-ID")],
    _: Principal = Depends(require_pilot_service),
    session: AsyncSession = Depends(get_db_session),
) -> PilotRunSnapshot:
    try:
        return await service(session).snapshot(
            run_id=run_id,
            owner_actor_id=delegated_actor(delegated_actor_id),
        )
    except PermissionError as exc:
        raise ForbiddenError(
            "The requested run is not available"
        ) from exc


@router.post("/runs/{run_id}/commands", response_model=PilotRunSnapshot)
async def submit_command(
    run_id: UUID,
    raw_command: Annotated[dict, Body()],
    _: Principal = Depends(require_pilot_service),
    session: AsyncSession = Depends(get_db_session),
) -> PilotRunSnapshot:
    try:
        command: PilotRunCommand = PILOT_COMMAND_ADAPTER.validate_python(raw_command)
    except PydanticValidationError as exc:
        raise BadRequestError("The command is invalid") from exc
    try:
        if isinstance(command, PilotResumeCommand):
            return await service(session).resume(run_id=run_id, command=command)
        if isinstance(command, PilotCancelCommand):
            return await service(session).cancel(run_id=run_id, command=command)
    except PermissionError as exc:
        raise ForbiddenError(
            "The requested run is not available"
        ) from exc
    raise BadRequestError("The command is invalid")


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: UUID,
    delegated_actor_id: Annotated[str, Header(alias="X-Delegated-Actor-ID")],
    _: Principal = Depends(require_pilot_service),
    header_cursor: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    query_cursor: Annotated[str | None, Query(alias="last_event_id")] = None,
) -> StreamingResponse:
    if header_cursor and query_cursor and header_cursor != query_cursor:
        raise BadRequestError(
            "Conflicting event cursors were supplied"
        )
    cursor_value = header_cursor or query_cursor
    try:
        cursor = UUID(cursor_value) if cursor_value else None
    except ValueError as exc:
        raise BadRequestError(
            "The event cursor is invalid"
        ) from exc
    actor_id = delegated_actor(delegated_actor_id)

    async def event_source() -> AsyncIterator[str]:
        pubsub = get_redis().client.pubsub()
        channel = pilot_run_events_channel(str(run_id))
        await pubsub.subscribe(channel)
        sent: set[str] = set()
        try:
            async with get_postgres().session_factory() as replay_session:
                replay = PilotService(PilotRepository(replay_session))
                try:
                    events = await replay.events(
                        run_id=run_id,
                        owner_actor_id=actor_id,
                        after_event_id=cursor,
                    )
                except PermissionError as exc:
                    raise ForbiddenError(
                        "The requested run is not available"
                    ) from exc
                for event in events:
                    event_id = str(event["event_id"])
                    sent.add(event_id)
                    yield encode_sse(event)

            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=10.0,
                )
                if message is None:
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(0)
                    continue
                raw = message["data"]
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                event = json.loads(raw)
                event_id = str(event.get("event_id") or "")
                if not event_id or event_id in sent:
                    continue
                sent.add(event_id)
                yield encode_sse(event)
                if event.get("type") in {"completed", "failed"} or (
                    event.get("type") == "status"
                    and event.get("data", {}).get("status") == "cancelled"
                ):
                    return
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


def encode_sse(event: dict) -> str:
    payload = json.dumps(
        event, ensure_ascii=False, separators=(",", ":"), default=str
    )
    return (
        f"id: {event['event_id']}\n"
        "event: run-event\n"
        f"data: {payload}\n\n"
    )


@router.get(
    "/citations/{evidence_id}/source",
    response_model=SourceResolution,
)
async def citation_source(
    evidence_id: UUID,
    delegated_actor_id: Annotated[str, Header(alias="X-Delegated-Actor-ID")],
    _: Principal = Depends(require_pilot_service),
    session: AsyncSession = Depends(get_db_session),
) -> SourceResolution:
    try:
        return await service(session).citation_source(
            evidence_id=evidence_id,
            owner_actor_id=delegated_actor(delegated_actor_id),
        )
    except PermissionError as exc:
        raise ForbiddenError(
            "The requested citation is not available"
        ) from exc
