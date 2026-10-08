"""环境恢复：真迁移/事务/并发，执行探测用可控替身；不执行模型。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from test_workbench_ledger_db import OTHER, OWNER, handover, seed
from test_workbench_ledger_db import db as db  # noqa: F401

from app.application.workbench.ledger import Ledger
from app.application.workbench.runner import SandboxLink
from app.bootstrap.workbench import build_runner
from app.domain.workbench.states import AttemptState, TaskState, WaitingReason
from app.infrastructure.workbench.publisher import RedisPublisher
from app.infrastructure.workbench.repository import WorkbenchRepository


async def waiting(factory, reason=WaitingReason.ENVIRONMENT):
    env, sb, sid = await seed(factory)
    async with factory() as s:
        repo = WorkbenchRepository(s)
        led = Ledger(repo)
        task = (await led.handover(handover(sid), owner_actor_id=OWNER))["task_id"]
        await led.transition(task, TaskState.VALIDATING)
        await led.transition(task, TaskState.QUEUED)
        attempt = (
            await led.open_attempt(task, step_id="sql_generate", step_version="1")
        )["attempt_id"]
        await led.attempt_transition(attempt, AttemptState.RUNNING, turn_id="old-turn")
        await led.attempt_transition(
            attempt, AttemptState.FAILED, failure_code="environment", retryable=True
        )
        await led.transition(
            task, TaskState.WAITING, waiting_reason=reason, current_step=1
        )
        async with repo.transaction():
            await repo.set_environment_status(env, "online")
    return env, sb, sid, task, attempt


async def queue(factory, env, owner=OWNER):
    async with factory() as s:
        repo = WorkbenchRepository(s)
        async with repo.transaction():
            return await repo.queue_environment_recovery(env, owner_actor_id=owner)


async def commands(factory, kind):
    async with factory() as s:
        return (
            (
                await s.execute(
                    text("select * from workbench_commands where kind = :kind"),
                    {"kind": kind},
                )
            )
            .mappings()
            .all()
        )


async def test_heartbeat_probe_deduplicates_concurrent_pending_and_claimed(db):
    env, _, sid, task, _ = await waiting(db)
    assert await queue(db, env, OTHER) == 0
    counts = await asyncio.gather(*(queue(db, env) for _ in range(6)))
    assert sum(counts) == 1
    probes = await commands(db, "task.reconnect")
    assert len(probes) == 1 and probes[0]["payload"] == {
        "task_id": task,
        "environment_id": env,
    }
    async with db() as s:
        # 模拟 runner 已认领；心跳不能再创建第二条。
        await s.execute(
            text(
                "update workbench_commands set status='claimed' where session_id=:sid"
            ),
            {"sid": sid},
        )
        await s.commit()
    assert await queue(db, env) == 0


@pytest.mark.parametrize(
    "reason", [WaitingReason.INPUT, WaitingReason.APPROVAL, WaitingReason.RESOURCE]
)
async def test_machine_online_does_not_resume_human_or_budget_wait(db, reason):
    env, _, sid, task, _ = await waiting(db, reason)
    assert await queue(db, env) == 0
    async with db() as s:
        assert not await Ledger(WorkbenchRepository(s)).resume_environment(
            task, session_id=sid, environment_id=env
        )
    assert await commands(db, "task.drive") == []


async def test_resume_once_preserves_step_failed_attempt_and_enqueues_atomically(db):
    env, _, sid, task, attempt = await waiting(db)

    async def resume():
        async with db() as s:
            return await Ledger(WorkbenchRepository(s)).resume_environment(
                task, session_id=sid, environment_id=env
            )

    assert sum(await asyncio.gather(*(resume() for _ in range(6)))) == 1
    assert len(await commands(db, "task.drive")) == 1
    async with db() as s:
        repo = WorkbenchRepository(s)
        current = await repo.get_task(task)
        assert current["state"] == "QUEUED" and current["current_step"] == 1
        assert current["active_attempt_id"] is None
        failed = await repo.get_attempt(attempt)
        assert failed["status"] == "FAILED" and failed["turn_ids"] == ["old-turn"]
        events = await repo.list_events(session_id=sid)
        resumed = [
            e
            for e in events
            if e["type"] == "task/state" and e["payload"]["from"] == "WAITING"
        ]
        assert len(resumed) == 1 and resumed[0]["payload"]["to"] == "QUEUED"


async def test_cancel_races_resume_without_reviving_task(db):
    env, _, sid, task, _ = await waiting(db)

    async def resume():
        async with db() as s:
            return await Ledger(WorkbenchRepository(s)).resume_environment(
                task, session_id=sid, environment_id=env
            )

    async def cancel():
        async with db() as s:
            return await Ledger(WorkbenchRepository(s)).request_cancel(
                task, owner_actor_id=OWNER
            )

    await asyncio.gather(resume(), cancel())
    assert not await resume()
    assert await queue(db, env) == 0
    async with db() as s:
        repo = WorkbenchRepository(s)
        assert (await repo.get_task(task))["state"] == "CANCELLED"
        session = await repo.get_session(sid)
        assert session["wheel"] == "user" and session["active_task_id"] is None


async def test_enqueue_failure_rolls_back_task_state_and_event(db):
    env, _, sid, task, _ = await waiting(db)
    async with db() as s:
        repo = WorkbenchRepository(s)
        repo.enqueue_command = AsyncMock(side_effect=RuntimeError("queue unavailable"))
        with pytest.raises(RuntimeError, match="queue unavailable"):
            await Ledger(repo).resume_environment(
                task, session_id=sid, environment_id=env
            )
    async with db() as s:
        repo = WorkbenchRepository(s)
        assert (await repo.get_task(task))["state"] == "WAITING"
        events = await repo.list_events(session_id=sid)
        assert not any(
            e["type"] == "task/state" and e["payload"]["from"] == "WAITING"
            for e in events
        )
    assert await commands(db, "task.drive") == []


async def test_probe_timeout_keeps_waiting_without_model_command(db):
    env, _, sid, task, _ = await waiting(db)
    runner = build_runner(db, publisher=RedisPublisher(None, "test"), runner_id="r")
    client = AsyncMock()
    client.request.side_effect = TimeoutError()
    link = AsyncMock()
    link.ensure_connected.return_value = client
    async with db() as s:
        session = await WorkbenchRepository(s).get_session(sid)
    await runner._reconnect_task(
        {"task_id": task, "environment_id": env}, session, link
    )
    async with db() as s:
        assert (await WorkbenchRepository(s).get_task(task))["state"] == "WAITING"
    assert await commands(db, "task.drive") == []


@pytest.mark.parametrize("status", ["disconnected", "pending", "unknown", "ready"])
async def test_runner_probes_without_model_and_resumes_only_ready(db, status):
    env, _, sid, task, _ = await waiting(db)
    assert await queue(db, env) == 1
    runner = build_runner(
        db, publisher=RedisPublisher(None, "test"), runner_id="new-runner"
    )
    client = AsyncMock()
    client.request.side_effect = [{"shell": "powershell"}, {"status": status}]
    link = AsyncMock()
    link.ensure_connected.return_value = client
    runner.link_for = AsyncMock(return_value=link)
    # 新 runner 从持久命令队列认领恢复探测，不依赖旧进程内存。
    assert await runner.run_once() == 1
    assert [call.args[0] for call in client.request.call_args_list] == [
        "environment/info",
        "environment/status",
    ]
    async with db() as s:
        current = await WorkbenchRepository(s).get_task(task)
    assert current["state"] == ("QUEUED" if status == "ready" else "WAITING")
    assert len(await commands(db, "task.drive")) == (1 if status == "ready" else 0)
    assert await queue(db, env) == (0 if status == "ready" else 1)


async def test_offline_or_cancelled_probe_does_not_contact_execution_server(db):
    env, _, sid, task, _ = await waiting(db)
    runner = build_runner(db, publisher=RedisPublisher(None, "test"), runner_id="r")
    link = AsyncMock()
    async with db() as s:
        repo = WorkbenchRepository(s)
        session = await repo.get_session(sid)
        async with repo.transaction():
            await repo.set_environment_status(env, "offline")
    await runner._reconnect_task(
        {"task_id": task, "environment_id": env}, session, link
    )
    link.ensure_connected.assert_not_called()


def test_environment_notifications_are_thread_scoped_and_short_recovery_is_not_failure():
    link = SandboxLink(None, {"id": "sandbox"})
    link.turn_waiters = {
        "a": {"thread_id": "thread-a", "env_lost": False, "error": None},
        "b": {"thread_id": "thread-b", "env_lost": False, "error": None},
    }
    link._feed_turn_waiters("thread/environment/disconnected", {"threadId": "thread-a"})
    assert link.turn_waiters["a"]["env_lost"] and not link.turn_waiters["b"]["env_lost"]
    link._feed_turn_waiters("thread/environment/connected", {"threadId": "thread-a"})
    assert (
        not link.turn_waiters["a"]["env_lost"]
        and link.turn_waiters["a"]["error"] is None
    )
