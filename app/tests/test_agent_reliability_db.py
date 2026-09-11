"""Fault and concurrency tests against an isolated PostgreSQL database.

AGENT_TEST_DATABASE_URL must explicitly name a disposable *_tests database.
Every test creates/drops only its own randomly named schema and runs the real
migration chain. These checks are intentionally not replaced by SQL mocks.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.agent.event_sink import DBEventSink
from app.application.agent.run_service import AgentRunService
from app.application.agent.side_effect_service import (
    DurableSideEffectService,
    UnknownSideEffect,
)
from app.domain.agent.commands import CreateRunCommand, ResumeRunCommand
from app.domain.agent.executor import ExecutionBinding, ExecutionEvent
from app.domain.agent.models import DomainEvent, RunLineage, UserInput
from app.infrastructure.agent.delivery import AgentDelivery
from app.infrastructure.agent.effects import EffectRepository
from app.infrastructure.agent.pilot_repository import PilotRepository
from app.infrastructure.agent.repositories import AgentRepository
from app.infrastructure.agent.transactions import LeaseLost
from app.infrastructure.graph.executor import GraphExecutor
from app.infrastructure.graph.walking_skeleton import build_walking_skeleton_graph
from app.tasks.agent_delivery import execute_command, pump

ROOT = Path(__file__).resolve().parents[1]


def migrate(connection):
    modules = []
    with Operations.context(MigrationContext.configure(connection)):
        for path in sorted((ROOT / "alembic/versions").glob("20*.py")):
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
            modules.append(module)
    return modules


@pytest_asyncio.fixture
async def db():
    url = os.environ.get("AGENT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set AGENT_TEST_DATABASE_URL to run PostgreSQL fault tests")
    assert url.rsplit("/", 1)[-1].endswith("_tests"), (
        "use a disposable *_tests database"
    )
    schema = "agent_test_" + uuid.uuid4().hex
    sync = create_engine(url.replace("postgresql://", "postgresql+psycopg://"))
    with sync.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        migrate(connection)
    engine = create_async_engine(
        url.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args={"server_settings": {"search_path": schema}},
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    yield sessions
    await engine.dispose()
    with sync.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    sync.dispose()


async def scalar(db, sql, **params):
    async with db() as s:
        return (await s.execute(text(sql), params)).scalar_one()


async def sql(db, query, **params):
    async with db() as s, s.begin():
        await s.execute(text(query), params)


async def phase0(db, *, initial=""):
    async with db() as s:
        repo = AgentRepository(s)
        session_id = await repo.create_session()
        result = await AgentRunService(repo).create_run(
            CreateRunCommand(
                session_id=session_id,
                idempotency_key="request-1",
                user_input=UserInput(text=initial),
            )
        )
    run_id = str(result["run_id"])
    command_id = await scalar(
        db,
        "select id from outbox_message where topic='agent.execution' and aggregate_key=:id",
        id=run_id,
    )
    return run_id, session_id, command_id


def graph_executor():
    return GraphExecutor(
        build_walking_skeleton_graph, provider="phase0-v1", resume_field="user_input"
    )


async def expire(db):
    await sql(
        db,
        "update agent_execution_leases set expires_at=clock_timestamp()-interval '1 second'",
    )


@pytest.mark.asyncio
async def test_domain_ui_outbox_and_state_rollback_together(db, monkeypatch):
    run_id, session_id, _ = await phase0(db)
    before = await scalar(db, "select count(*) from session_events")
    async with db() as s:
        repo = AgentRepository(s)
        original = repo.append_event

        async def fail_ui(event, category):
            if category == "ui":
                raise RuntimeError("crash between domain and UI")
            return await original(event, category)

        monkeypatch.setattr(repo, "append_event", fail_ui)
        with pytest.raises(RuntimeError, match="between domain"):
            async with repo.transaction():
                await repo.set_run_status(
                    run_id=run_id, session_id=session_id, status="running"
                )
                await DBEventSink(repo).append(
                    DomainEvent(
                        type="RunStarted",
                        lineage=RunLineage(session_id=session_id, run_id=run_id),
                    )
                )
    assert await scalar(db, "select count(*) from session_events") == before
    assert await scalar(db, "select status from agent_runs") == "created"
    assert await scalar(db, "select count(*) from outbox_message") == 3


@pytest.mark.asyncio
async def test_duplicate_create_has_one_command_and_one_initial_fact(db):
    _, session_id, _ = await phase0(db)

    async def create():
        async with db() as s:
            return await AgentRunService(AgentRepository(s)).create_run(
                CreateRunCommand(session_id=session_id, idempotency_key="request-1")
            )

    results = await asyncio.gather(create(), create())
    assert str(results[0]["run_id"]) == str(results[1]["run_id"])
    assert await scalar(db, "select count(*) from agent_runs") == 1
    assert (
        await scalar(
            db, "select count(*) from outbox_message where topic='agent.execution'"
        )
        == 1
    )


@pytest.mark.asyncio
async def test_start_resume_and_duplicate_delivery_use_durable_binding(db):
    run_id, _, command = await phase0(db)
    await execute_command(str(command), sessions=db, executor=graph_executor())
    assert await scalar(db, "select status from agent_runs") == "waiting"
    token = await scalar(db, "select resume_token from agent_runs")
    async with db() as s:
        await AgentRunService(AgentRepository(s)).resume_run(
            ResumeRunCommand(
                run_id=run_id,
                resume_token=token,
                user_input=UserInput(text="confirmed"),
            )
        )
    resume = await scalar(
        db, "select id from outbox_message where deduplication_key like 'resume:%'"
    )
    await asyncio.gather(
        execute_command(str(resume), sessions=db, executor=graph_executor()),
        execute_command(str(resume), sessions=db, executor=graph_executor()),
    )
    await execute_command(str(command), sessions=db, executor=graph_executor())
    await execute_command(str(resume), sessions=db, executor=graph_executor())
    assert await scalar(db, "select status from agent_runs") == "completed"
    assert await scalar(db, "select count(*) from tool_side_effects") == 1
    assert (
        await scalar(
            db, "select count(*) from session_events where event_type='RunCompleted'"
        )
        == 1
    )
    assert await scalar(db, "select count(*) from inbox_message") == 2


@pytest.mark.asyncio
async def test_expired_worker_cannot_write_fail_or_release_replacement(db):
    run_id, session_id, command = await phase0(db)
    delivery = AgentDelivery(db)
    first, _ = await delivery.claim_execution(command)
    await expire(db)
    second, _ = await delivery.claim_execution(command)
    assert second.epoch > first.epoch
    async with db() as s:
        repo = AgentRepository(s, lease=first)
        with pytest.raises(LeaseLost):
            await repo.set_run_status(
                run_id=run_id, session_id=session_id, status="failed"
            )
        with pytest.raises(LeaseLost):
            await repo.append_event(
                DomainEvent(
                    type="RunFailed",
                    lineage=RunLineage(session_id=session_id, run_id=run_id),
                ),
                "domain",
            )
    await delivery.release(first)
    await delivery.renew(second)
    assert await scalar(db, "select status from agent_runs") == "created"


@pytest.mark.asyncio
async def test_publisher_crash_duplicate_delivery_reconcile_and_dead_letter(db):
    _, _, command = await phase0(db, initial="go")
    delivery = AgentDelivery(db, lease_seconds=1, max_attempts=2)
    # Simulate broker acceptance followed by a publisher crash before marking.
    message = await delivery.claim_delivery()
    await sql(
        db,
        "update outbox_message set lease_expires_at=clock_timestamp()-interval '1 second' where id=:id",
        id=message["id"],
    )
    retry = await delivery.claim_delivery()
    assert retry["id"] == message["id"]
    with pytest.raises(LeaseLost):
        await delivery.finish_delivery(message)
    await delivery.finish_delivery(retry, error="broker_down")
    assert (
        await scalar(
            db, "select count(*) from outbox_dead_letter where replayed_at is null"
        )
        == 1
    )
    await delivery.replay(message["id"])
    assert (
        await scalar(
            db,
            "select attempt_count from outbox_message where id=:id",
            id=message["id"],
        )
        == 0
    )
    delivered = []

    async def broker(msg):
        delivered.append(msg["id"])

    await pump(sessions=db, publish=broker)
    await sql(
        db,
        "update outbox_message set published_at=clock_timestamp()-interval '2 minutes'",
    )
    assert await delivery.reconcile() == 1
    await pump(sessions=db, publish=broker)
    assert delivered.count(command) == 2
    await execute_command(str(command), sessions=db, executor=graph_executor())
    await sql(
        db,
        "update outbox_message set published_at=clock_timestamp()-interval '2 minutes'",
    )
    assert await delivery.reconcile() == 0


@pytest.mark.asyncio
async def test_process_death_is_reclaimed_and_heartbeat_keeps_live_work(db):
    _, _, command = await phase0(db, initial="go")
    delivery = AgentDelivery(db, lease_seconds=1)
    abandoned, _ = await delivery.claim_execution(command)
    await expire(db)  # process died without releasing or acknowledging

    class SlowGraph(GraphExecutor):
        async def events(self, binding, *, after=0):
            await asyncio.sleep(1.4)
            async for event in super().events(binding, after=after):
                yield event

    executor = SlowGraph(
        build_walking_skeleton_graph, provider="phase0-v1", resume_field="user_input"
    )
    await execute_command(str(command), sessions=db, executor=executor, lease_seconds=1)
    assert await scalar(db, "select status from agent_runs") == "completed"
    assert (
        await scalar(db, "select epoch from agent_execution_leases") > abandoned.epoch
    )


async def pilot(db):
    actor = uuid.uuid4()
    async with db() as s:
        run, _ = await PilotRepository(s).create_run(
            owner_actor_id=actor,
            idempotency_key=uuid.uuid4(),
            title="test",
            user_input="question",
        )
    return run, actor


@pytest.mark.asyncio
async def test_pilot_resume_outbox_failure_rolls_back_token_consumption(
    db, monkeypatch
):
    run, actor = await pilot(db)
    action = uuid.uuid4()
    async with db() as s:
        repo = PilotRepository(s)
        await repo.set_status(
            run_id=run["id"], session_id=run["session_id"], status="running"
        )
        await repo.set_status(
            run_id=run["id"],
            session_id=run["session_id"],
            status="waiting",
            resume_token=str(action),
        )

        async def fail(**kwargs):
            raise RuntimeError("outbox insert failed")

        monkeypatch.setattr(repo, "enqueue", fail)
        with pytest.raises(RuntimeError, match="outbox insert"):
            await repo.consume_resume(
                run_id=run["id"],
                owner_actor_id=actor,
                action_id=action,
                idempotency_key=uuid.uuid4(),
                value="yes",
            )
    assert (
        await scalar(db, "select resume_idempotency_key from agent_pilot_controls")
        is None
    )
    assert await scalar(db, "select resume_token from agent_runs") == str(action)
    assert (
        await scalar(
            db,
            "select count(*) from outbox_message where deduplication_key like 'resume:%'",
        )
        == 0
    )


class BlockingExecutor:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def start(self, request):
        return ExecutionBinding(execution_id=request.execution_id, provider="fake")

    async def events(self, binding, *, after=0):
        self.started.set()
        await self.release.wait()
        yield ExecutionEvent(
            sequence=1,
            binding=binding.model_copy(
                update={"status": "completed", "state": {"summary": "late result"}}
            ),
        )

    async def cancel(self, binding):
        return binding.model_copy(update={"status": "cancelled"})

    async def close(self, binding):
        self.closed = True


@pytest.mark.asyncio
async def test_cancel_fences_late_completion_and_preserves_cancel_event(db):
    run, actor = await pilot(db)
    command = await scalar(
        db, "select id from outbox_message where topic='agent.execution'"
    )
    executor = BlockingExecutor()

    async def prepare(_):
        return {}

    job = asyncio.create_task(
        execute_command(
            str(command), sessions=db, executor=executor, prepare_input=prepare
        )
    )
    await asyncio.wait_for(executor.started.wait(), 5)
    async with db() as s:
        await PilotRepository(s).request_cancel(run_id=run["id"], owner_actor_id=actor)
    executor.release.set()
    with pytest.raises(LeaseLost):
        await job
    assert await scalar(db, "select status from agent_runs") == "cancelled"
    assert (
        await scalar(
            db, "select count(*) from session_events where payload->>'type'='completed'"
        )
        == 0
    )
    assert executor.closed


@pytest.mark.asyncio
async def test_remote_success_local_crash_reconciles_without_repeating_effect(
    db, monkeypatch
):
    run_id, _, command = await phase0(db)
    delivery = AgentDelivery(db)
    lease, _ = await delivery.claim_execution(command)

    class Remote:
        def __init__(self):
            self.calls = 0
            self.receipt = None

        async def execute(self, **kwargs):
            self.calls += 1
            self.receipt = {"id": "remote-1", "confirmed": True}
            return self.receipt

        async def lookup(self, **kwargs):
            return self.receipt

    remote = Remote()
    async with db() as s:
        repo = EffectRepository(s, lease=lease)

        async def crash(*args, **kwargs):
            raise LeaseLost("process died after remote success")

        monkeypatch.setattr(repo, "settle", crash)
        with pytest.raises(LeaseLost):
            await DurableSideEffectService(repo, remote).execute(
                key="business-action-1", run_id=run_id, intent={"action": "publish"}
            )
    await expire(db)
    await delivery.reconcile()
    assert await scalar(db, "select status from tool_side_effects") == "unknown"
    replacement, _ = await delivery.claim_execution(command)
    async with db() as s:
        repo = EffectRepository(s, lease=replacement)
        result = await DurableSideEffectService(repo, remote).execute(
            key="business-action-1", run_id=run_id, intent={"action": "publish"}
        )
    assert result == remote.receipt
    assert remote.calls == 1
    assert await scalar(db, "select status from tool_side_effects") == "completed"


@pytest.mark.asyncio
async def test_unknown_remote_result_is_not_blindly_reexecuted(db):
    run_id, _, command = await phase0(db)
    lease, _ = await AgentDelivery(db).claim_execution(command)

    class Remote:
        calls = 0

        async def execute(self, **kwargs):
            self.calls += 1
            raise TimeoutError("response lost")

        async def lookup(self, **kwargs):
            return None

    remote = Remote()
    async with db() as s:
        service = DurableSideEffectService(EffectRepository(s, lease=lease), remote)
        with pytest.raises(TimeoutError):
            await service.execute(
                key="action-1", run_id=run_id, intent={"action": "publish"}
            )
        with pytest.raises(UnknownSideEffect):
            await service.execute(
                key="action-1", run_id=run_id, intent={"action": "publish"}
            )
    assert remote.calls == 1


@pytest.mark.asyncio
async def test_actual_postgres_checkpoints_are_isolated_by_execution(db):
    from contextlib import contextmanager

    from langgraph.checkpoint.postgres import PostgresSaver

    url = os.environ["AGENT_TEST_DATABASE_URL"]
    async with db() as s:
        schema = (await s.execute(text("select current_schema()"))).scalar_one()

    @contextmanager
    def saver():
        with PostgresSaver.from_conn_string(
            url + f"?options=-csearch_path%3D{schema}"
        ) as checkpointer:
            yield checkpointer

    run_id, _, command = await phase0(db)
    first = GraphExecutor(
        build_walking_skeleton_graph,
        provider="phase0-v1",
        resume_field="user_input",
        checkpointer_factory=saver,
    )
    await execute_command(str(command), sessions=db, executor=first)
    token = await scalar(db, "select resume_token from agent_runs")
    async with db() as s:
        await AgentRunService(AgentRepository(s)).resume_run(
            ResumeRunCommand(
                run_id=run_id, resume_token=token, user_input=UserInput(text="go")
            )
        )
    resume = await scalar(
        db, "select id from outbox_message where deduplication_key like 'resume:%'"
    )
    second = GraphExecutor(
        build_walking_skeleton_graph,
        provider="phase0-v1",
        resume_field="user_input",
        checkpointer_factory=saver,
    )
    await execute_command(str(resume), sessions=db, executor=second)
    assert await scalar(db, "select count(distinct thread_id) from checkpoints") == 2
    assert await scalar(db, "select status from agent_runs") == "completed"


@pytest.mark.asyncio
async def test_pilot_resume_replay_and_snapshot_are_consistent(db):
    from app.application.agent.pilot_service import PilotService
    from app.infrastructure.graph.executor import GraphExecutor
    from app.infrastructure.graph.pilot_graph import build_pilot_graph

    run, actor = await pilot(db)
    command = await scalar(
        db, "select id from outbox_message where topic='agent.execution'"
    )

    def executor():
        return GraphExecutor(
            build_pilot_graph, provider="pilot-v1", resume_field="approval"
        )

    async def prepare(_):
        return {
            "run_id": str(run["id"]),
            "user_input": "question",
            "draft": "answer",
            "citations": [
                {
                    "evidence_id": str(actor),
                    "knowledge_document_id": str(actor),
                    "knowledge_document_version_id": str(actor),
                    "chunk_id": str(actor),
                    "title": "evidence",
                    "quote": "test quote",
                    "source_document_id": str(actor),
                    "source_document_version_id": str(actor),
                    "content_hash": "a" * 64,
                    "source_href": f"/api/web/v1/citations/{actor}/source",
                }
            ],
        }

    await execute_command(
        str(command), sessions=db, executor=executor(), prepare_input=prepare
    )
    token = await scalar(db, "select resume_token from agent_runs")
    key = uuid.uuid4()
    async with db() as s:
        repo = PilotRepository(s)
        await repo.consume_resume(
            run_id=run["id"],
            owner_actor_id=actor,
            action_id=uuid.UUID(token),
            idempotency_key=key,
            value="confirm",
        )
        _, consumed = await repo.consume_resume(
            run_id=run["id"],
            owner_actor_id=actor,
            action_id=uuid.UUID(token),
            idempotency_key=key,
            value="confirm",
        )
        assert not consumed
        snapshot = await PilotService(repo).snapshot(
            run_id=run["id"], owner_actor_id=actor
        )
        assert snapshot.status == "queued"
        assert snapshot.required_action is None
        with pytest.raises(ValueError, match="different input"):
            await repo.consume_resume(
                run_id=run["id"],
                owner_actor_id=actor,
                action_id=uuid.UUID(token),
                idempotency_key=key,
                value="reject",
            )
    resume = await scalar(
        db, "select id from outbox_message where deduplication_key like 'resume:%'"
    )
    await execute_command(str(resume), sessions=db, executor=executor())
    assert await scalar(db, "select status from agent_runs") == "completed"
    assert (
        await scalar(
            db, "select count(*) from session_events where payload->>'type'='completed'"
        )
        == 1
    )
    assert await scalar(db, "select count(*) from inbox_message") == 2


@pytest.mark.asyncio
async def test_schema_upgrade_downgrade_preserves_existing_run(db):
    from sqlalchemy import create_engine

    run_id, _, _ = await phase0(db)
    async with db() as s:
        schema = (await s.execute(text("select current_schema()"))).scalar_one()
    path = ROOT / "alembic/versions/20260911_0007_durable_delivery.py"
    spec = importlib.util.spec_from_file_location("reliability_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous_spec = importlib.util.spec_from_file_location(
        "previous_reliability_migration",
        ROOT / "alembic/versions/20260910_0006_agent_reliability.py",
    )
    previous = importlib.util.module_from_spec(previous_spec)
    previous_spec.loader.exec_module(previous)
    engine = create_engine(
        os.environ["AGENT_TEST_DATABASE_URL"].replace(
            "postgresql://", "postgresql+psycopg://"
        )
    )
    with engine.begin() as connection:
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
            previous.downgrade()
            previous.upgrade()
            module.upgrade()
        assert (
            str(connection.execute(text("select id from agent_runs")).scalar_one())
            == run_id
        )
    engine.dispose()


@pytest.mark.asyncio
async def test_resume_response_loss_reuses_command_but_rejects_changed_input(db):
    run_id, _, command = await phase0(db)
    await execute_command(str(command), sessions=db, executor=graph_executor())
    token = await scalar(db, "select resume_token from agent_runs")
    request = ResumeRunCommand(
        run_id=run_id, resume_token=token, user_input=UserInput(text="yes")
    )
    async with db() as s:
        service = AgentRunService(AgentRepository(s))
        first = await service.resume_run(request)
        second = await service.resume_run(request)
        assert first == second
        with pytest.raises(ValueError, match="different input"):
            await service.resume_run(
                request.model_copy(update={"user_input": UserInput(text="no")})
            )
    assert (
        await scalar(
            db,
            "select count(*) from outbox_message where deduplication_key like 'resume:%'",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_poison_consumer_delivery_is_bounded_and_visible(db):
    _, _, command = await phase0(db)
    await sql(
        db,
        "update outbox_message set status='published', attempt_count=10, published_at=clock_timestamp()-interval '5 minutes' where id=:id",
        id=command,
    )
    delivery = AgentDelivery(db)
    await delivery.reconcile()
    assert (
        await scalar(
            db,
            "select error_code from outbox_dead_letter where message_id=:id",
            id=command,
        )
        == "consumer_unacknowledged"
    )
    assert await delivery.claim_execution(command) is None
    await delivery.replay(command)
    assert await delivery.claim_execution(command) is not None


@pytest.mark.asyncio
async def test_effect_entry_rejects_stale_lease_and_cross_session_settlement(db):
    run_id, _, command = await phase0(db)
    delivery = AgentDelivery(db)
    old, _ = await delivery.claim_execution(command)
    async with db() as s:
        repo = EffectRepository(s, lease=old)
        await repo.prepare(key="owned-effect", run_id=run_id, intent={"x": 1})
        assert await repo.begin("owned-effect")
    _, _, other_command = await phase0(db)
    other, _ = await delivery.claim_execution(other_command)
    async with db() as s:
        repo = EffectRepository(s, lease=other)
        with pytest.raises(ValueError, match="leased session"):
            await repo.prepare(key="other-effect", run_id=run_id, intent={"x": 1})
        with pytest.raises(ValueError, match="no longer writable"):
            await repo.settle("owned-effect", status="completed", receipt={"bad": True})
    await expire(db)
    async with db() as s:
        with pytest.raises(LeaseLost):
            await EffectRepository(s, lease=old).prepare(
                key="late-effect", run_id=run_id, intent={"x": 1}
            )
    assert await scalar(db, "select count(*) from tool_side_effects") == 1
    assert await scalar(db, "select status from tool_side_effects") == "executing"
