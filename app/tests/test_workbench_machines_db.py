"""我的机器：账上的三件事（报到、置离线、列身份）与一次真实的对账。真数据库。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import text
from test_workbench_ledger_db import OTHER, OWNER
from test_workbench_ledger_db import db as db  # noqa: F401

from app.application.workbench.machines import MachineSync
from app.application.workbench.project_service import ProjectService
from app.infrastructure.workbench.repository import (
    SqlWorkbenchStores,
    WorkbenchRepository,
)

CEILING = {"sandbox": "workspace-write", "network": False}


class Relay:
    def __init__(self, online):
        self.online = online

    async def agents(self):
        return self.online


def agent(name, roots):
    return {
        "codex": "0.155.1",
        "software": "0.2.0",
        "since": 1,
        "machine": {"name": name, "roots": roots, "ceiling": CEILING},
    }


@asynccontextmanager
async def book(factory):
    async with factory() as session:
        repo = WorkbenchRepository(session)
        async with repo.transaction():
            yield repo


async def identity(factory, owner, relay_user):
    async with book(factory) as repo:
        await repo.put_relay_identity(
            owner,
            relay_user=relay_user,
            agent_token_ciphertext="a",
            sandbox_token_ciphertext="s",
        )


async def test_reporting_twice_keeps_one_machine_and_refreshes_it(db):
    async with book(db) as repo:
        first = await repo.report_environment(
            owner_actor_id=OWNER,
            name="laptop",
            agent_version="0.2.0",
            codex_version="0.155.1",
            roots=["/home/u/a"],
            ceiling=CEILING,
        )
    async with book(db) as repo:
        await repo.set_environment_status(first, "offline")
    async with book(db) as repo:
        again = await repo.report_environment(
            owner_actor_id=OWNER,
            name="laptop",
            agent_version="0.3.0",
            codex_version="0.156.0",
            roots=["/home/u/a", "/home/u/b"],
            ceiling={"sandbox": "read-only", "network": True},
        )
        other = await repo.report_environment(
            owner_actor_id=OTHER,
            name="laptop",
            agent_version=None,
            codex_version=None,
            roots=[],
            ceiling={},
        )
    assert again == first and other != first
    async with db() as session:
        mine = await WorkbenchRepository(session).list_environments(
            owner_actor_id=OWNER
        )
    assert len(mine) == 1
    env = mine[0]
    assert env["status"] == "online" and env["last_seen_at"] is not None
    assert env["roots"] == ["/home/u/a", "/home/u/b"]
    assert env["ceiling"] == {"sandbox": "read-only", "network": True}
    assert env["agent_version"] == "0.3.0" and env["codex_version"] == "0.156.0"


async def test_going_offline_touches_only_that_persons_online_machines(db):
    async with book(db) as repo:
        ids = {}
        for owner, name in ((OWNER, "a"), (OWNER, "b"), (OTHER, "c")):
            ids[name] = await repo.report_environment(
                owner_actor_id=owner,
                name=name,
                agent_version=None,
                codex_version=None,
                roots=[],
                ceiling={},
            )
    async with book(db) as repo:
        assert await repo.set_environments_offline(OWNER, except_id=ids["a"]) == 1
    async with book(db) as repo:
        # 已经离线的不再算
        assert await repo.set_environments_offline(OWNER, except_id=ids["a"]) == 0
    async with db() as session:
        rows = (
            await session.execute(
                text("select name, status from workbench_environments order by name")
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("a", "online"),
        ("b", "offline"),
        ("c", "online"),
    ]
    async with book(db) as repo:
        assert await repo.set_environments_offline(OWNER) == 1


async def test_identities_are_listed_without_their_tokens(db):
    await identity(db, OWNER, "u-owner")
    await identity(db, OTHER, "u-other")
    async with db() as session:
        listed = await WorkbenchRepository(session).list_relay_identities()
    assert [(str(i["owner_actor_id"]), i["relay_user"]) for i in listed] == [
        (OWNER, "u-owner"),
        (OTHER, "u-other"),
    ]
    assert all(set(i) == {"owner_actor_id", "relay_user"} for i in listed)


async def test_an_agent_coming_and_going_shows_up_as_a_workspace_then_offline(db):
    await identity(db, OWNER, "u-owner")
    relay = Relay({"u-owner": agent("laptop", ["/home/u/research"])})
    sync = MachineSync(SqlWorkbenchStores(db), relay)

    assert await sync.sync_once() == {"online": 1, "offline": 0, "untouched": 0}
    async with db() as session:
        workspaces = await ProjectService(WorkbenchRepository(session)).workspaces(
            owner_actor_id=OWNER
        )
    assert [(w["environment_name"], w["root"]) for w in workspaces] == [
        ("laptop", "/home/u/research")
    ]

    # 同一台机器再报一次：还是那一台
    assert await sync.sync_once() == {"online": 1, "offline": 0, "untouched": 0}
    # 换了一台机器接进来：旧的离线
    relay.online = {"u-owner": agent("desktop", ["/data/work"])}
    assert await sync.sync_once() == {"online": 1, "offline": 1, "untouched": 0}
    # 代理关了：全部离线
    relay.online = {}
    assert await sync.sync_once() == {"online": 0, "offline": 1, "untouched": 0}
    async with db() as session:
        envs = await WorkbenchRepository(session).list_environments(
            owner_actor_id=OWNER
        )
    assert {e["name"]: e["status"] for e in envs} == {
        "laptop": "offline",
        "desktop": "offline",
    }
