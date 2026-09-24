"""Prove the Agent overlay shares policy and preserves rollback/death semantics."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import DBAPIError
from test_agent_reliability_db import db as db
from test_agent_reliability_db import expire, graph_executor, phase0, scalar, sql

from app.infrastructure.agent.delivery import AgentDelivery
from app.infrastructure.messaging.durable_delivery import DurableDelivery
from app.tasks.agent_delivery import execute_command

ROOT = Path(__file__).resolve().parents[1]


async def revision(db, direction):
    path = ROOT / "alembic/versions/20260911_0007_durable_delivery.py"
    spec = importlib.util.spec_from_file_location("shared_agent_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def invoke(connection):
        with Operations.context(MigrationContext.configure(connection)):
            getattr(module, direction)()

    async with db.kw["bind"].begin() as connection:
        await connection.run_sync(invoke)


def test_agent_transport_methods_are_the_template_policy():
    for name in ("claim_delivery", "finish_delivery", "replay", "_dead_letter"):
        assert getattr(AgentDelivery, name) is getattr(DurableDelivery, name)
    with pytest.raises(ValueError, match="invalid delivery policy"):
        AgentDelivery(None, max_attempts=0)


async def test_nonempty_dead_letter_migration_and_new_receipts_round_trip(db):
    _, _, first = await phase0(db)
    _, _, second = await phase0(db)
    await revision(db, "downgrade")
    await sql(
        db,
        "INSERT INTO agent_delivery_failures(message_id,error_code,failed_at) "
        "VALUES (:id,'old-failure','2026-09-01T00:00:00Z')",
        id=first,
    )
    await revision(db, "upgrade")
    assert await scalar(db, "SELECT count(*) FROM outbox_dead_letter") == 1
    assert (
        await scalar(db, "SELECT count(*) FROM agent_delivery_failures_legacy_0006")
        == 1
    )
    for statement in (
        "DELETE FROM agent_delivery_failures_legacy_0006",
        "TRUNCATE agent_delivery_failures_legacy_0006",
    ):
        with pytest.raises(DBAPIError, match="read-only"):
            await sql(db, statement)
    with pytest.raises(DBAPIError):
        await sql(db, "SELECT * FROM agent_delivery_failures")
    delivery = AgentDelivery(db)
    await delivery.replay(first)
    async with db() as session, session.begin():
        await delivery._dead_letter(session, second, "new-failure")
    snapshot_sql = (
        "SELECT jsonb_agg(jsonb_build_object('id',message_id,'error',error_code,"
        "'failed',failed_at,'replayed',replayed_at) ORDER BY message_id) FROM "
    )
    before = await scalar(db, snapshot_sql + "outbox_dead_letter")
    await revision(db, "downgrade")
    assert await scalar(db, snapshot_sql + "agent_delivery_failures") == before
    await revision(db, "upgrade")
    assert await scalar(db, snapshot_sql + "outbox_dead_letter") == before
    assert await delivery.claim_execution(second) is None


async def test_reconcile_respects_live_agent_epoch_then_bounds_abandoned_work(db):
    _, _, command = await phase0(db)
    await sql(
        db,
        "UPDATE outbox_message SET status='published',attempt_count=10,"
        "published_at=clock_timestamp()-interval '5 minutes' WHERE id=:id",
        id=command,
    )
    delivery = AgentDelivery(db)
    assert await delivery.claim_execution(command)
    assert await delivery.reconcile() == 0
    assert await scalar(db, "SELECT count(*) FROM outbox_dead_letter") == 0
    with pytest.raises(RuntimeError, match="active executions"):
        await revision(db, "downgrade")
    await expire(db)
    await delivery.reconcile()
    assert await scalar(db, "SELECT count(*) FROM outbox_dead_letter") == 1
    assert await delivery.claim_execution(command) is None


async def test_publisher_crash_attempts_are_bounded_by_shared_policy(db):
    _, _, command = await phase0(db)
    await sql(db, "UPDATE outbox_message SET attempt_count=10 WHERE id=:id", id=command)
    await AgentDelivery(db).reconcile()
    assert (
        await scalar(
            db,
            "SELECT error_code FROM outbox_dead_letter WHERE message_id=:id",
            id=command,
        )
        == "delivery_attempts_exhausted"
    )


async def test_downgrade_refuses_unconsumed_non_agent_state(db):
    await sql(
        db,
        "INSERT INTO outbox_message(id,topic,aggregate_key,deduplication_key,payload,headers) "
        "VALUES(gen_random_uuid(),'future.task.v1','one','future-one','{}','{}')",
    )
    with pytest.raises(RuntimeError, match="non-Agent"):
        await revision(db, "downgrade")
    assert await scalar(db, "SELECT count(*) FROM outbox_message") == 1


async def test_fresh_process_death_recovered_with_a_new_agent_epoch(db):
    _, _, command = await phase0(db, initial="complete")
    schema = await scalar(db, "SELECT current_schema()")
    child = """
import asyncio, os, uuid
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.infrastructure.agent.delivery import AgentDelivery
async def main():
    engine = create_async_engine(
        os.environ['AGENT_TEST_DATABASE_URL'].replace('postgresql://','postgresql+asyncpg://'),
        connect_args={'server_settings': {'search_path': os.environ['AGENT_TEST_SCHEMA']}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    claim = await AgentDelivery(sessions).claim_execution(uuid.UUID(os.environ['AGENT_TEST_COMMAND']))
    assert claim is not None
    print('LEASE_CLAIMED', flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        child,
        env={
            **os.environ,
            "AGENT_TEST_SCHEMA": schema,
            "AGENT_TEST_COMMAND": str(command),
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert (
            await asyncio.wait_for(process.stdout.readline(), timeout=15)
            == b"LEASE_CLAIMED\n"
        )
        process.kill()
        await process.wait()
        assert process.returncode != 0
        await expire(db)
        await execute_command(str(command), sessions=db, executor=graph_executor())
        assert await scalar(db, "SELECT status FROM agent_runs") == "completed"
        assert await scalar(db, "SELECT count(*) FROM inbox_message") == 1
        assert await scalar(db, "SELECT epoch FROM agent_execution_leases") == 2
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
