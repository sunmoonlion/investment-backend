"""缺数据的需求（账 56）：各用户查不到的公司汇总给管理端；只汇总，不说是谁。真数据库。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from test_workbench_ledger_db import OTHER, OWNER
from test_workbench_ledger_db import db as db  # noqa: F401
from test_workbench_routes import principal

from app.application.workbench.demand import Demand
from app.bootstrap.api import create_app
from app.infrastructure.storage.postgres import get_db_session
from app.infrastructure.workbench.repository import WorkbenchRepository
from app.interfaces.http.middleware.auth import require_investment_admin
from core.config import Settings

URL = "/api/admin/v1/workbench/missing-data"


def missing(code: str, dataset: str | None, source: str = "tool") -> dict:
    market = "sh" if dataset and dataset.startswith("sh") else None
    return {
        "security_code": code,
        "market": market,
        "dataset": dataset,
        "source": source,
    }


async def conversation(repo: WorkbenchRepository, owner: str) -> str:
    sandbox = await repo.register_sandbox(
        owner_actor_id=owner,
        app_server_url="ws://sandbox:47800",
        token_ref="inline:t",
        codex_version="0.155.1",
    )
    return await repo.create_session(
        owner_actor_id=owner,
        environment_id=None,
        sandbox_id=sandbox,
        project_root=None,
        thread_settings={},
        kind="chat",
    )


async def world(db):
    """两个人：OWNER 两段对话里查过茅台（两个数据集）、平安银行；OTHER 查过茅台、专家说招行没入库。"""
    async with db() as s:
        repo = WorkbenchRepository(s)
        async with repo.transaction():
            first = await conversation(repo, OWNER)
            second = await conversation(repo, OWNER)
            third = await conversation(repo, OTHER)
            plan = [
                (first, missing("600519", "sh600519-financials")),
                (first, missing("000001", "sz000001-financials")),
                (second, missing("600519", "sh600519-prices")),
                (third, missing("600519", "sh600519-financials")),
                (third, missing("600036", None, "expert")),
            ]
        # 一条一个事务：时间才分得出先后（同一事务里 now() 是同一个值）
        for sid, payload in plan:
            async with repo.transaction():
                await repo.append_event(
                    session_id=sid,
                    kind="log",
                    event_type="data.missing",
                    payload=payload,
                )
        # 别的事件不算
        async with repo.transaction():
            await repo.append_event(
                session_id=first, kind="log", event_type="turn/completed", payload={}
            )


async def test_summary_by_company_most_recent_first(db):
    await world(db)
    async with db() as s:
        rows = await Demand(WorkbenchRepository(s)).summary()
    assert [r["security_code"] for r in rows] == ["600036", "600519", "000001"]
    moutai = rows[1]
    assert (moutai["times"], moutai["users"]) == (3, 2)
    assert moutai["market"] == "sh"
    assert moutai["datasets"] == ["sh600519-financials", "sh600519-prices"]
    assert rows[0]["datasets"] == [] and rows[0]["market"] is None
    assert rows[2] == {
        **rows[2],
        "times": 1,
        "users": 1,
        "datasets": ["sz000001-financials"],
    }
    now = datetime.now(UTC)
    for r in rows:
        assert now - timedelta(minutes=5) < r["last_at"] <= now + timedelta(minutes=1)
    # 不说是谁查的
    assert not any("actor" in k or "owner" in k or "session" in k for k in rows[0])


async def test_the_admin_page_reads_it_and_nobody_else(db):
    await world(db)
    app = create_app(Settings())

    async def override_db():
        async with db() as s:
            yield s

    app.dependency_overrides[get_db_session] = override_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        # 没有管理端的登录：进不来
        assert (await http.get(URL)).status_code == 401
        app.dependency_overrides[require_investment_admin] = lambda: principal(
            str(uuid.uuid4())
        )
        got = await http.get(URL)
        assert got.status_code == 200
        items = got.json()["items"]
        assert [i["security_code"] for i in items] == ["600036", "600519", "000001"]
        assert items[1]["times"] == 3 and items[1]["users"] == 2
        assert items[1]["datasets"] == ["sh600519-financials", "sh600519-prices"]
        assert items[0]["last_at"].endswith("Z") or "+" in items[0]["last_at"]
        assert set(items[0]) == {
            "security_code",
            "market",
            "times",
            "users",
            "last_at",
            "datasets",
        }
        only_one = await http.get(URL, params={"limit": 1})
        assert [i["security_code"] for i in only_one.json()["items"]] == ["600036"]
        assert (await http.get(URL, params={"limit": 0})).status_code == 422
