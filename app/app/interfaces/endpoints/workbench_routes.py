"""工作台的网页接口（通道①）。身份从 web 浏览器会话取（OIDC），不信任客户端自报；每次读取重新授权（I3）。

路径前缀 /api/workbench。开关 WORKBENCH_ENABLED。契约版本 1；改字段按 C-C4 双端一起测。
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors.exceptions import AppException
from app.application.workbench.ledger import Ledger
from app.application.workbench.session_service import SessionService
from app.domain.security import Principal
from app.domain.workbench.errors import WorkbenchError
from app.domain.workbench.models import HandoverRequest
from app.infrastructure.storage.postgres import get_db_session, get_postgres
from app.infrastructure.storage.redis import get_redis
from app.infrastructure.workbench.repository import WorkbenchRepository
from app.interfaces.http.middleware.auth import get_web_current_user
from core.config import get_settings

CONTRACT_VERSION = 1


def require_workbench_enabled() -> None:
    if not get_settings().workbench_enabled:
        raise HTTPException(status_code=404, detail="workbench is disabled")


router = APIRouter(
    prefix="/workbench",
    tags=["Workbench"],
    dependencies=[Depends(require_workbench_enabled)],
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterEnvironment(Strict):
    name: str = Field(min_length=1, max_length=128)
    agent_version: str | None = None
    codex_version: str | None = None
    roots: list[str] = Field(default_factory=list)
    ceiling: dict[str, Any] = Field(
        default_factory=lambda: {"sandbox": "workspace-write", "network": False}
    )


class RegisterSandbox(Strict):
    app_server_url: str = Field(pattern=r"^wss?://")
    token_ref: str = Field(pattern=r"^(env|file|inline):")
    codex_version: str | None = None


class CreateSession(Strict):
    environment_id: str
    sandbox_id: str
    project_root: str = Field(min_length=1)
    thread_settings: dict[str, Any] | None = None


class StartTurn(Strict):
    text: str = Field(min_length=1, max_length=20000)
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()), max_length=128)


class Interrupt(Strict):
    turn_id: str | None = None


class Handover(Strict):
    idempotency_key: str = Field(min_length=8, max_length=128)
    profile_id: str = Field(min_length=1, max_length=128)
    profile_version: str | None = None
    original_input: dict[str, Any]
    attachments: list[str] = Field(default_factory=list)
    budget_limit: Decimal = Field(gt=0)
    budget_currency: str = "CNY"
    requested_deadline: str | None = None
    client_context: dict[str, Any] = Field(default_factory=dict)


class RespondInteraction(Strict):
    token: str = Field(min_length=16)
    decision: str | None = None
    answer: dict[str, Any] | None = None
    amount: str | None = None
    subject_digest: str | None = None


def _actor(principal: Principal) -> str:
    if principal.actor_id is None:
        raise HTTPException(status_code=403, detail="principal has no actor id")
    return str(principal.actor_id)


def _plain(d: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in d.items():
        if k in ("token_hash",):
            continue
        out[k] = str(v) if isinstance(v, uuid.UUID) else v
    return out


def _http(exc: WorkbenchError) -> AppException:
    # 走统一的 problem+json：顶层 code / status / detail
    return AppException(code=exc.code, status_code=exc.http_status, msg=exc.message)


async def _publish_commands_wakeup() -> None:
    try:
        await get_redis().client.publish(
            f"{get_settings().workbench_redis_key_prefix}:commands", "1"
        )
    except Exception:  # noqa: BLE001
        pass


# ---------------- environments / sandboxes ----------------
@router.get("/environments")
async def list_environments(
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    return {
        "contract_version": CONTRACT_VERSION,
        "environments": [
            _plain(e)
            for e in await repo.list_environments(owner_actor_id=_actor(principal))
        ],
    }


@router.post("/environments", status_code=201)
async def register_environment(
    body: RegisterEnvironment,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    async with repo.transaction():
        env_id = await repo.register_environment(
            owner_actor_id=_actor(principal),
            name=body.name,
            agent_version=body.agent_version,
            codex_version=body.codex_version,
            roots=body.roots,
            ceiling=body.ceiling,
        )
    return {"environment_id": env_id}


@router.get("/sandboxes")
async def list_sandboxes(
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    return {
        "contract_version": CONTRACT_VERSION,
        "sandboxes": [
            _plain(x)
            for x in await repo.list_sandboxes(owner_actor_id=_actor(principal))
        ],
    }


@router.post("/sandboxes", status_code=201)
async def register_sandbox(
    body: RegisterSandbox,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    async with repo.transaction():
        sb_id = await repo.register_sandbox(
            owner_actor_id=_actor(principal),
            app_server_url=body.app_server_url,
            token_ref=body.token_ref,
            codex_version=body.codex_version,
        )
    return {"sandbox_id": sb_id}


# ---------------- sessions ----------------
@router.post("/sessions", status_code=201)
async def create_session(
    body: CreateSession,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    try:
        result = await SessionService(repo).create(
            owner_actor_id=_actor(principal),
            environment_id=body.environment_id,
            sandbox_id=body.sandbox_id,
            project_root=body.project_root,
            thread_settings=body.thread_settings,
        )
        async with repo.transaction():
            await repo.enqueue_command(
                session_id=result["session_id"],
                sandbox_id=body.sandbox_id,
                kind="session.start_thread",
                payload={},
            )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    await _publish_commands_wakeup()
    return result


@router.get("/sessions")
async def list_sessions(
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    return {
        "contract_version": CONTRACT_VERSION,
        "sessions": [
            _plain(x)
            for x in await repo.list_sessions(owner_actor_id=_actor(principal))
        ],
    }


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        return await SessionService(WorkbenchRepository(session)).view(
            session_id, owner_actor_id=_actor(principal)
        )
    except WorkbenchError as exc:
        raise _http(exc) from exc


@router.post("/sessions/{session_id}/turns", status_code=202)
async def start_turn(
    session_id: str,
    body: StartTurn,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    try:
        event = await SessionService(repo).record_user_turn_requested(
            session_id,
            owner_actor_id=_actor(principal),
            text=body.text,
            request_id=body.request_id,
        )
        s = await repo.get_session(session_id)
        async with repo.transaction():
            cid = await repo.enqueue_command(
                session_id=session_id,
                sandbox_id=str(s["sandbox_id"]),
                kind="turn.start",
                payload={
                    "text": body.text,
                    "request_id": body.request_id,
                    "by": "user",
                },
            )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    await _publish_commands_wakeup()
    return {"request_id": body.request_id, "command_id": cid, "cursor": event["cursor"]}


@router.post("/sessions/{session_id}/interrupt", status_code=202)
async def interrupt(
    session_id: str,
    body: Interrupt,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    try:
        s = await SessionService(repo).assert_user_may_drive(
            session_id, owner_actor_id=_actor(principal)
        )
        async with repo.transaction():
            cid = await repo.enqueue_command(
                session_id=session_id,
                sandbox_id=str(s["sandbox_id"]),
                kind="turn.interrupt",
                payload={"turn_id": body.turn_id},
            )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    await _publish_commands_wakeup()
    return {"command_id": cid}


@router.get("/sessions/{session_id}/events")
async def list_events(
    session_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    try:
        await repo.get_session(session_id, owner_actor_id=_actor(principal))
    except WorkbenchError as exc:
        raise _http(exc) from exc
    events = await repo.list_events(
        session_id=session_id, after_cursor=after, limit=limit
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "events": events,
        "next_cursor": events[-1]["cursor"] if events else after,
    }


@router.get("/sessions/{session_id}/stream")
async def stream_events(
    session_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """SSE：先订阅 Redis 再回放数据库（否则两者之间提交的事件会丢），按 cursor 去重。"""
    repo = WorkbenchRepository(session)
    try:
        await repo.get_session(session_id, owner_actor_id=_actor(principal))
    except WorkbenchError as exc:
        raise _http(exc) from exc
    channel = f"{get_settings().workbench_redis_key_prefix}:session:{session_id}:events"

    def sse(ev: dict[str, Any]) -> str:
        return f"id: {ev.get('cursor')}\nevent: {ev.get('type', 'message')}\ndata: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

    async def gen():
        redis = get_redis().client
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)
        last = after
        try:
            async with get_postgres().session_factory() as s2:
                for ev in await WorkbenchRepository(s2).list_events(
                    session_id=session_id, after_cursor=after, limit=1000
                ):
                    last = max(last, int(ev["cursor"]))
                    yield sse(ev)
            async for message in pubsub.listen():
                if await request.is_disconnected():
                    break
                if message["type"] != "message":
                    continue
                ev = json.loads(message["data"])
                if int(ev.get("cursor", 0)) <= last:
                    continue
                last = int(ev["cursor"])
                yield sse(ev)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------- handover / tasks ----------------
@router.post("/sessions/{session_id}/handover", status_code=201)
async def handover(
    session_id: str,
    body: Handover,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    req = HandoverRequest(session_id=session_id, **body.model_dump())
    try:
        result = await Ledger(repo).handover(req, owner_actor_id=_actor(principal))
        if result["created"]:
            s = await repo.get_session(session_id)
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=session_id,
                    sandbox_id=str(s["sandbox_id"]),
                    kind="task.drive",
                    payload={"task_id": result["task_id"]},
                )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    await _publish_commands_wakeup()
    return result


@router.get("/tasks")
async def list_tasks(
    session_id: str | None = None,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    return {
        "contract_version": CONTRACT_VERSION,
        "tasks": [
            _plain(t)
            for t in await repo.list_tasks(
                owner_actor_id=_actor(principal), session_id=session_id
            )
        ],
    }


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        return await Ledger(WorkbenchRepository(session)).task_view(
            task_id, owner_actor_id=_actor(principal)
        )
    except WorkbenchError as exc:
        raise _http(exc) from exc


@router.post("/tasks/{task_id}/cancel", status_code=202)
async def cancel_task(
    task_id: str,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    try:
        return await Ledger(WorkbenchRepository(session)).request_cancel(
            task_id, owner_actor_id=_actor(principal)
        )
    except WorkbenchError as exc:
        raise _http(exc) from exc


# ---------------- interactions ----------------
@router.post("/interactions/{interaction_id}/respond")
async def respond_interaction(
    interaction_id: str,
    body: RespondInteraction,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    response: dict[str, Any] = {
        k: v
        for k, v in {
            "decision": body.decision,
            "answer": body.answer,
            "amount": body.amount,
        }.items()
        if v is not None
    }
    try:
        it = await repo.get_interaction(interaction_id)
        result = await Ledger(repo).respond_interaction(
            interaction_id,
            token=body.token,
            response=response,
            owner_actor_id=_actor(principal),
            subject_digest=body.subject_digest,
        )
        if it["kind"] == "tool_approval":
            s = await repo.get_session(str(it["session_id"]))
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=str(it["session_id"]),
                    sandbox_id=str(s["sandbox_id"]),
                    kind="approval.respond",
                    payload={
                        "request_id": it["prompt"]["subject"]["request_id"],
                        "decision": body.decision or "decline",
                    },
                )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    await _publish_commands_wakeup()
    return result
