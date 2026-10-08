"""Real transaction + outbox coverage, isolated test schema; no production DB."""

import asyncio

# pytest imports the shared schema fixture under its required name.
# ruff: noqa: F811
from copy import deepcopy

import pytest
from sqlalchemy import text
from test_workbench_agent_reports import envelope
from test_workbench_ledger_db import OWNER, db, seed  # noqa: F401

from app.application.workbench.agent_reports import record_agent_reports
from app.application.workbench.machines import MachineSync
from app.infrastructure.workbench.repository import (
    SqlWorkbenchStores,
    WorkbenchRepository,
)


async def prepare(factory):
    env, _, sid = await seed(factory)
    item = envelope()
    async with factory() as s:
        repo = WorkbenchRepository(s)
        async with repo.transaction():
            await repo.set_session_thread(sid, item["report"]["threadId"])
            await repo.put_relay_identity(
                OWNER,
                relay_user="stage2-test",
                agent_token_ciphertext="fixture",
                sandbox_token_ciphertext="fixture",
            )
    return env, sid, item


async def records(factory, sid):
    async with factory() as s:
        return await WorkbenchRepository(s).list_record_events(
            session_id=sid, types=("agent/localPermission",)
        )


async def test_receipt_observes_committed_audit_and_retry_has_one_event(db):
    env, sid, item = await prepare(db)

    class Relay:
        receipts = []

        async def agents(self):
            return {
                "stage2-test": {
                    "machine": {
                        "name": "laptop",
                        "roots": ["/home/u/research"],
                        "ceiling": {"sandbox": "workspace-write", "network": False},
                    },
                    "permission_reports": [item],
                }
            }

        async def permission_receipts(self, receipts):
            # A different connection must see the committed record before ack.
            rows = await records(db, sid)
            assert len(rows) == 1 and rows[0]["payload"] == item["report"]
            self.receipts.extend(receipts)

    relay = Relay()
    for _ in range(2):
        await MachineSync(SqlWorkbenchStores(db), relay).sync_once()
    assert relay.receipts == [{"receipt": item["receipt"], "status": "recorded"}] * 2
    assert len(await records(db, sid)) == 1


async def test_concurrent_duplicate_report_is_serialized(db):
    env, sid, item = await prepare(db)

    async def save():
        async with db() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                return await record_agent_reports(
                    repo, owner=OWNER, environment_id=env, reports=[item]
                )

    results = await asyncio.gather(save(), save())
    assert all(
        r == [{"receipt": item["receipt"], "status": "recorded"}] for r in results
    )
    assert len(await records(db, sid)) == 1
    changed = deepcopy(item)
    changed["report"]["scope"]["network"] = True
    async with db() as s:
        repo = WorkbenchRepository(s)
        async with repo.transaction():
            assert await record_agent_reports(
                repo, owner=OWNER, environment_id=env, reports=[changed]
            ) == [{"receipt": item["receipt"], "status": "rejected"}]


async def test_rollback_removes_audit_and_its_outbox(db):
    env, sid, item = await prepare(db)
    async with db() as s:
        before = (
            await s.execute(text("select count(*) from outbox_message"))
        ).scalar_one()
    async with db() as s:
        repo = WorkbenchRepository(s)
        with pytest.raises(RuntimeError, match="rollback fixture"):
            async with repo.transaction():
                assert (
                    await record_agent_reports(
                        repo, owner=OWNER, environment_id=env, reports=[item]
                    )
                )[0]["status"] == "recorded"
                raise RuntimeError("rollback fixture")
    assert await records(db, sid) == []
    async with db() as s:
        assert (
            await s.execute(text("select count(*) from outbox_message"))
        ).scalar_one() == before
