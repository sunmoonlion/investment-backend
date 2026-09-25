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
from app.application.workbench.provisioning import (
    HttpProvisioner,
    ProvisionerConfig,
    ProvisioningUnavailable,
    SandboxProvisioning,
    WsRelayAdmin,
)
from app.application.workbench.session_service import SessionService
from app.application.workbench.tokens import TokenIssuer
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


class Prefs(Strict):
    model: str | None = Field(default=None, max_length=128)
    approval_policy: str = Field(
        default="on-request", pattern=r"^(untrusted|on-request|on-failure|never)$"
    )


class AddCredential(Strict):
    provider: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=8, max_length=512)
    sandbox_id: str | None = None


class Conclusion(Strict):
    text: str = Field(max_length=20000)


def credential_cipher():
    """BYOK 凭据的对称加密（Fernet）；未配置密钥即 503。测试用依赖覆盖注入。"""
    key = get_settings().workbench_credential_key
    if not key:
        raise AppException(
            code="credential_store_unconfigured",
            status_code=503,
            msg="credential store is not configured",
        )
    from cryptography.fernet import Fernet

    return Fernet(key.encode())


def provisioning_backends():
    """供给器与会合点管理通道的客户端；未配置即 503。测试用依赖覆盖注入假的。"""
    s = get_settings()
    if not (
        s.workbench_provisioner_url
        and s.workbench_provisioner_token
        and s.workbench_relay_admin_url
        and s.workbench_relay_admin_token
    ):
        raise _http(ProvisioningUnavailable("sandbox provisioning is not configured"))
    provisioner = HttpProvisioner(
        ProvisionerConfig(
            url=s.workbench_provisioner_url,
            token=s.workbench_provisioner_token,
            model_provider=s.workbench_sandbox_model_provider,
            model=s.workbench_sandbox_model,
            provider_base_url=s.workbench_sandbox_provider_base_url,
        )
    )
    relay_admin = WsRelayAdmin(
        s.workbench_relay_admin_url, s.workbench_relay_admin_token
    )
    return provisioner, relay_admin, s.workbench_relay_public_url


def token_issuer() -> TokenIssuer | None:
    """D10：配置了签名私钥才签 JWT；否则供给走不透明随机令牌。"""
    s = get_settings()
    if not s.workbench_token_signing_key:
        return None
    return TokenIssuer(s.workbench_token_signing_key, issuer=s.workbench_token_issuer)


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
        thread_settings = body.thread_settings
        if thread_settings is None:  # 用户设置面的偏好进 thread 设置（0002「设置」）
            prefs = await repo.get_prefs(_actor(principal))
            thread_settings = {
                "approvalPolicy": prefs["approval_policy"],
                "sandbox": "workspace-write",
            }
            if prefs["model"]:
                thread_settings["model"] = prefs["model"]
        result = await SessionService(repo).create(
            owner_actor_id=_actor(principal),
            environment_id=body.environment_id,
            sandbox_id=body.sandbox_id,
            project_root=body.project_root,
            thread_settings=thread_settings,
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


def sse_frame(ev: dict[str, Any]) -> str:
    """一帧 SSE。不带 `event:` 名：浏览器 EventSource 只把无名（或名为 message）的帧交给 onmessage，
    带了事件名（如 turn/completed）的帧会被静默丢掉。事件类型在 data 里的 `type` 字段。"""
    return f"id: {ev.get('cursor')}\ndata: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"


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
                    yield sse_frame(ev)
            async for message in pubsub.listen():
                if await request.is_disconnected():
                    break
                if message["type"] != "message":
                    continue
                ev = json.loads(message["data"])
                if int(ev.get("cursor", 0)) <= last:
                    continue
                last = int(ev["cursor"])
                yield sse_frame(ev)
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


@router.get("/tasks/{task_id}/artifacts")
async def task_artifacts(
    task_id: str,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """底稿页：每个交回物的最新版本带内容。不渲染任何评级、目标价字段（F-WEB-05 由网页守，后端只给事实）。"""
    repo = WorkbenchRepository(session)
    try:
        await repo.get_task(task_id, owner_actor_id=_actor(principal))
    except WorkbenchError as exc:
        raise _http(exc) from exc
    arts = await repo.list_artifacts_with_content(task_id)
    return {
        "contract_version": CONTRACT_VERSION,
        "artifacts": [_plain(a) for a in arts],
    }


@router.put("/tasks/{task_id}/conclusion")
async def put_conclusion(
    task_id: str,
    body: Conclusion,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """结论栏是用户草稿：落为 conclusion 交回物的新版本，永远标 kind=user_draft。"""
    repo = WorkbenchRepository(session)
    try:
        await repo.get_task(task_id, owner_actor_id=_actor(principal))
        async with repo.transaction():
            art = await repo.put_artifact(
                task_id=task_id,
                attempt_id=None,
                name="conclusion",
                kind="user_draft",
                content={"text": body.text, "author": "user"},
            )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return _plain(art)


# ---------------- sandbox provisioning (0003 D9) ----------------
def _provisioning(
    session: AsyncSession, principal: Principal, cipher, backends, issuer=None
) -> SandboxProvisioning:
    provisioner, relay_admin, relay_public_url = backends
    return SandboxProvisioning(
        WorkbenchRepository(session),
        cipher=cipher,
        provisioner=provisioner,
        relay_admin=relay_admin,
        relay_public_url=relay_public_url,
        issuer=issuer,
    )


@router.get("/token-keys")
async def token_keys(issuer: TokenIssuer | None = Depends(token_issuer)):
    """公钥集（JWKS）：边缘与知识服务用它就地验工作台签发的令牌（D10）。未配签名密钥时为空。"""
    if issuer is None:
        return {"keys": []}
    return issuer.public_jwks()


@router.post("/sandboxes/provision")
async def provision_sandbox(
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
    cipher=Depends(credential_cipher),
    backends=Depends(provisioning_backends),
    issuer: TokenIssuer | None = Depends(token_issuer),
):
    """用设置页登记的 key 给这个用户拉起（或更新）他的沙箱。首次同时签发会合点身份，代理令牌只在这次响应里。"""
    try:
        result = await _provisioning(
            session, principal, cipher, backends, issuer
        ).provision(_actor(principal))
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return {"contract_version": CONTRACT_VERSION, **result}


@router.post("/sandboxes/relay-identity/rotate")
async def rotate_relay_identity(
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
    cipher=Depends(credential_cipher),
    backends=Depends(provisioning_backends),
    issuer: TokenIssuer | None = Depends(token_issuer),
):
    """撤换会合点令牌（D10）：旧的按 jti 吊销并推到会合点，新代理令牌只在这次响应里。沙箱在线则滚动。"""
    try:
        result = await _provisioning(
            session, principal, cipher, backends, issuer
        ).rotate_relay_identity(_actor(principal))
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return result


@router.get("/sandboxes/provisioned")
async def provisioned_sandbox_status(
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
    cipher=Depends(credential_cipher),
    backends=Depends(provisioning_backends),
):
    try:
        result = await _provisioning(session, principal, cipher, backends).status(
            _actor(principal)
        )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return {"contract_version": CONTRACT_VERSION, **result}


@router.delete("/sandboxes/provisioned")
async def deprovision_sandbox(
    purge: bool = Query(default=False),
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
    cipher=Depends(credential_cipher),
    backends=Depends(provisioning_backends),
):
    try:
        result = await _provisioning(session, principal, cipher, backends).deprovision(
            _actor(principal), purge=purge
        )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return {"contract_version": CONTRACT_VERSION, **result}


# ---------------- settings: prefs & credentials ----------------
@router.get("/prefs")
async def get_prefs(
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    prefs = await WorkbenchRepository(session).get_prefs(_actor(principal))
    return {"contract_version": CONTRACT_VERSION, **_plain(prefs)}


@router.put("/prefs")
async def put_prefs(
    body: Prefs,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    async with repo.transaction():
        prefs = await repo.put_prefs(
            _actor(principal), model=body.model, approval_policy=body.approval_policy
        )
    return {"contract_version": CONTRACT_VERSION, **_plain(prefs)}


@router.get("/credentials")
async def list_credentials(
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    rows = await WorkbenchRepository(session).list_credentials(_actor(principal))
    return {
        "contract_version": CONTRACT_VERSION,
        "credentials": [_plain(r) for r in rows],
    }


@router.post("/credentials", status_code=201)
async def add_credential(
    body: AddCredential,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
    cipher=Depends(credential_cipher),
):
    """key 只经 HTTPS 提交一次，服务端存密文，接口永不回显（F-WEB-01、C-D9）。"""
    repo = WorkbenchRepository(session)
    if body.sandbox_id:
        try:
            await repo.get_sandbox(body.sandbox_id, owner_actor_id=_actor(principal))
        except WorkbenchError as exc:
            raise _http(exc) from exc
    ciphertext = cipher.encrypt(body.api_key.encode()).decode()
    async with repo.transaction():
        row = await repo.add_credential(
            owner_actor_id=_actor(principal),
            sandbox_id=body.sandbox_id,
            provider=body.provider,
            ciphertext=ciphertext,
            hint=body.api_key[-4:],
        )
    return _plain(row)


@router.post("/credentials/{credential_id}/revoke")
async def revoke_credential(
    credential_id: str,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    repo = WorkbenchRepository(session)
    async with repo.transaction():
        ok = await repo.revoke_credential(
            credential_id, owner_actor_id=_actor(principal)
        )
    if not ok:
        raise AppException(
            code="credential_not_found", status_code=404, msg="no active credential"
        )
    return {"credential_id": credential_id, "status": "revoked"}


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
        if it["task_id"] is not None and result.get("state") == "QUEUED":
            s2 = await repo.get_session(str(it["session_id"]))
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=str(it["session_id"]),
                    sandbox_id=str(s2["sandbox_id"]),
                    kind="task.drive",
                    payload={"task_id": str(it["task_id"])},
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
