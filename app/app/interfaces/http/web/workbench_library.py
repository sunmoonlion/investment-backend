"""知识库（SDD 0011 第一期）：登录的用户看、读、改名、拿掉自己的底稿与交回物。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.workbench.library import Library
from app.bootstrap.workbench import workbench_store
from app.domain.security import Principal
from app.domain.workbench.errors import WorkbenchError
from app.infrastructure.storage.postgres import get_db_session
from app.interfaces.endpoints.workbench_routes import (
    CONTRACT_VERSION,
    _actor,
    _http,
    require_workbench_enabled,
)
from app.interfaces.http.middleware.auth import get_web_current_user

router = APIRouter(
    prefix="/workbench",
    tags=["Workbench"],
    dependencies=[Depends(require_workbench_enabled)],
)


class Rename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=400)


def _library(principal: Principal, session: AsyncSession) -> Library:
    return Library(workbench_store(session), owner_actor_id=_actor(principal))


@router.get("/library")
async def library_listing(
    kind: str | None = Query(default=None, pattern=r"^(dossier|deliverable)$"),
    project: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    items = await _library(principal, session).listing(
        kind=kind, project_id=project, q=q
    )
    return {"contract_version": CONTRACT_VERSION, "items": items}


@router.get("/library/{item_id}")
async def library_item(
    item_id: str,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        item = await _library(principal, session).item(item_id)
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return {"contract_version": CONTRACT_VERSION, "item": item}


@router.get("/library/{item_id}/versions/{version}/content")
async def library_content(
    item_id: str,
    version: int,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        got = await _library(principal, session).content(item_id, version)
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return {"contract_version": CONTRACT_VERSION, **got}


@router.patch("/library/{item_id}")
async def library_rename(
    item_id: str,
    body: Rename,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        item = await _library(principal, session).rename(item_id, body.title)
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return {"contract_version": CONTRACT_VERSION, "item": item}


@router.delete("/library/{item_id}", status_code=204)
async def library_remove(
    item_id: str,
    principal: Principal = Depends(get_web_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    try:
        await _library(principal, session).remove(item_id)
    except WorkbenchError as exc:
        raise _http(exc) from exc
    return Response(status_code=204)
