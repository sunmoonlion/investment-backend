"""专家：有哪些专家、请专家、委托的步骤、底稿、等我决定的事（PRD/apps/investment-expert.md）。

方法的原文不出后端：这里的任何接口都不返回它。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.workbench.delegation import DelegationService, expert_on_offer
from app.application.workbench.expert_desk import ExpertDesk
from app.bootstrap.workbench import workbench_store
from app.domain.security import Principal
from app.domain.workbench.errors import WorkbenchError
from app.domain.workbench.pack_view import pack_view
from app.domain.workbench.packs import listed_packs
from app.infrastructure.storage.postgres import get_db_session
from app.interfaces.endpoints.workbench_routes import (
    CONTRACT_VERSION,
    _actor,
    _http,
    _publish_commands_wakeup,
    require_workbench_enabled,
)
from app.interfaces.http.middleware.auth import get_web_current_user

router = APIRouter(
    prefix="/workbench",
    tags=["Workbench"],
    dependencies=[Depends(require_workbench_enabled)],
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Delegate(Strict):
    """请专家。专家看得到什么、能做什么由后端按项目定，这里不收任何设置。"""

    idempotency_key: str = Field(min_length=8, max_length=128)
    expert: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=4000)
    budget_limit: Decimal = Field(gt=0, le=Decimal("100000"))
    budget_currency: str = Field(default="CNY", pattern=r"^CNY$")


@router.get("/packs")
async def packs(
    principal: Principal = Depends(get_web_current_user),
) -> dict[str, Any]:
    """有哪些专家：名字、解决什么、不解决什么、每一步的名字与一句白话、不通过时怎么办。"""
    return {
        "contract_version": CONTRACT_VERSION,
        # 默认预算与币种所有者还没定（W4）：先不给默认值，页面让用户自己填
        "budget": {"currency": "CNY", "default": None},
        "packs": [pack_view(p) for p in listed_packs()],
    }


@router.get("/packs/{pack_id}")
async def one_pack(
    pack_id: str,
    principal: Principal = Depends(get_web_current_user),
) -> dict[str, Any]:
    try:
        pack = expert_on_offer(pack_id)
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return {"contract_version": CONTRACT_VERSION, "pack": pack_view(pack)}


@router.get("/tasks/{task_id}/steps")
async def task_steps(
    task_id: str,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """一个委托做到哪了：委托单、步骤轨。每一步的交回物与过程另取（下一个接口）。"""
    try:
        view = await ExpertDesk(workbench_store(session)).steps(
            task_id, owner_actor_id=_actor(principal)
        )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return {"contract_version": CONTRACT_VERSION, **view}


@router.get("/tasks/{task_id}/steps/{number}")
async def task_step(
    task_id: str,
    number: int = Path(ge=1, le=100),
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """一步的全部：每一次做的交回物、逐条的验收结果、过程。"""
    try:
        view = await ExpertDesk(workbench_store(session)).step(
            task_id, number, owner_actor_id=_actor(principal)
        )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return {"contract_version": CONTRACT_VERSION, **view}


@router.post("/projects/{project_id}/delegations", status_code=201)
async def delegate(
    project_id: str,
    body: Delegate,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """从专家入口请专家：新建一段工作对话并立刻交给专家。要么都成，要么什么都不留下。"""
    question = body.question.strip()
    try:
        if not question:
            raise WorkbenchError("the question is empty")
        made = await DelegationService(workbench_store(session)).from_expert_entry(
            owner_actor_id=_actor(principal),
            project_id=project_id,
            profile_id=body.expert,
            question=question,
            budget_limit=body.budget_limit,
            budget_currency=body.budget_currency,
            idempotency_key=body.idempotency_key,
        )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    if made["created"]:
        await _publish_commands_wakeup()
    return {"contract_version": CONTRACT_VERSION, **made}


@router.get("/expert/overview")
async def expert_overview(
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """专家首页：等我决定、进行中、最近交回的。"""
    view = await ExpertDesk(workbench_store(session)).overview(
        owner_actor_id=_actor(principal)
    )
    return {"contract_version": CONTRACT_VERSION, **view}


@router.get("/interactions")
async def interactions(
    status: str = Query(default="pending", pattern=r"^(pending|consumed|all)$"),
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """跨对话的待办。只给摘要；答复用的令牌不在这里。"""
    found = await ExpertDesk(workbench_store(session)).waiting_for_me(
        owner_actor_id=_actor(principal), status=None if status == "all" else status
    )
    return {"contract_version": CONTRACT_VERSION, "interactions": found}


@router.get("/interactions/{interaction_id}")
async def interaction(
    interaction_id: str,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """审查面：在哪、待决的是什么、决定的对象、时效、决定记录。"""
    try:
        view = await ExpertDesk(workbench_store(session)).review(
            interaction_id, owner_actor_id=_actor(principal)
        )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return {"contract_version": CONTRACT_VERSION, "interaction": view}


@router.get("/tasks/{task_id}/dossier")
async def dossier(
    task_id: str,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """底稿，按这位专家登记的排法排好。结论栏只放用户自己存过的。"""
    try:
        view = await ExpertDesk(workbench_store(session)).dossier(
            task_id, owner_actor_id=_actor(principal)
        )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return {"contract_version": CONTRACT_VERSION, **view}


@router.get("/tasks/{task_id}/dossier/export", response_class=PlainTextResponse)
async def dossier_export(
    task_id: str,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> PlainTextResponse:
    """整份底稿导出成 Markdown，连同出处与「我的结论」。"""
    try:
        body = await ExpertDesk(workbench_store(session)).dossier_export(
            task_id, owner_actor_id=_actor(principal)
        )
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return PlainTextResponse(
        body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="dossier-{task_id}.md"'},
    )
