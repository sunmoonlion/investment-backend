"""runner 与 app-server 客户端：假 app-server（进程内 WebSocket）+ 真数据库。
验：thread/start 落 thread_id、turn 命令、通知投影成事件（delta 不落）、用户自驾时审批变 Interaction 并把决定回给 app-server、命令认领互斥。"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from sqlalchemy import text
from test_workbench_ledger_db import OWNER  # noqa: F401  (fixture)
from test_workbench_ledger_db import db as db
from websockets.asyncio.server import serve

from app.application.workbench.ledger import Ledger
from app.application.workbench.runner import Publisher, Runner, event_kind, thread_id_of
from app.application.workbench.session_service import SessionService
from app.infrastructure.workbench.repository import WorkbenchRepository

TOKEN = "cap-token-for-tests"


class FakeAppServer:
    """够用的 app-server：initialize、thread/start、turn/start、turn/interrupt；turn 里按输入文本决定要不要发审批请求。"""

    def __init__(self):
        self.server = None
        self.port = 0
        self.elicitations: list[dict] = []
        self.requests: list[dict] = []
        self.auth_headers: list[str | None] = []
        self.approval_decisions: list[dict] = []
        self.ready = asyncio.Event()

    async def start(self):
        async def handler(ws):
            self.auth_headers.append(ws.request.headers.get("Authorization"))
            async for raw in ws:
                msg = json.loads(raw)
                if "method" in msg and "id" in msg:
                    self.requests.append(msg)
                    asyncio.create_task(self.handle(ws, msg))
                elif "id" in msg:  # response to our server request
                    self.approval_decisions.append(msg)
                    fut = self._pending.pop(msg["id"], None)
                    if fut:
                        fut.set_result(msg.get("result"))

        self._pending: dict[str, asyncio.Future] = {}
        self.server = await serve(handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def handle(self, ws, msg):
        m, p, rid = msg["method"], msg.get("params") or {}, msg["id"]
        send = lambda o: ws.send(json.dumps(o))  # noqa: E731
        if m == "initialize":
            await send(
                {
                    "id": rid,
                    "result": {"userAgent": "fake/0.155.1", "codexHome": "/data/codex"},
                }
            )
        elif m == "thread/start":
            tid = f"thread-{uuid.uuid4().hex[:8]}"
            await send(
                {"id": rid, "result": {"thread": {"id": tid, "cwd": p.get("cwd")}}}
            )
            await send({"method": "thread/started", "params": {"thread": {"id": tid}}})
        elif m == "turn/start":
            tid = p["threadId"]
            turn_id = f"turn-{uuid.uuid4().hex[:6]}"
            await send(
                {"id": rid, "result": {"turn": {"id": turn_id, "threadId": tid}}}
            )
            await send(
                {
                    "method": "turn/started",
                    "params": {"threadId": tid, "turn": {"id": turn_id}},
                }
            )
            await send(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": tid, "turnId": turn_id, "delta": "he"},
                }
            )
            text = p["input"][0]["text"]
            if "NEED_ELICITATION" in text:
                srid = f"elic-{uuid.uuid4().hex[:6]}"
                fut = asyncio.get_running_loop().create_future()
                self._pending[srid] = fut
                await send(
                    {
                        "id": srid,
                        "method": "mcpServer/elicitation/request",
                        "params": {
                            "message": "sunmoon_knowledge wants to run run_sql",
                            "mode": "form",
                            "requestedSchema": {
                                "type": "object",
                                "properties": {
                                    "confirm": {"type": "boolean"},
                                    "note": {"type": "string", "default": "ok"},
                                },
                            },
                        },
                    }
                )
                answer = await asyncio.wait_for(fut, 20)
                self.elicitations.append(answer)
                await send(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": tid,
                            "turnId": turn_id,
                            "item": {
                                "type": "mcpToolCall",
                                "id": "mcp-1",
                                "server": "sunmoon_knowledge",
                                "tool": "run_sql",
                                "status": "completed"
                                if answer.get("action") == "accept"
                                else "failed",
                            },
                        },
                    }
                )
            if "NEED_APPROVAL" in text:
                srid = f"req-{uuid.uuid4().hex[:6]}"
                fut = asyncio.get_running_loop().create_future()
                self._pending[srid] = fut
                await send(
                    {
                        "id": srid,
                        "method": "item/commandExecution/requestApproval",
                        "params": {
                            "threadId": tid,
                            "turnId": turn_id,
                            "itemId": "it-1",
                            "command": "touch /home/u/x",
                            "cwd": "/home/u/research/proj",
                            "reason": "write outside workspace",
                            "environmentId": "user-pc",
                        },
                    }
                )
                decision = await asyncio.wait_for(fut, 20)
                await send(
                    {
                        "method": "item/completed",
                        "params": {
                            "threadId": tid,
                            "turnId": turn_id,
                            "item": {
                                "type": "commandExecution",
                                "id": "it-1",
                                "status": "completed"
                                if decision.get("decision") != "decline"
                                else "declined",
                            },
                        },
                    }
                )
            await send(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": tid,
                        "turnId": turn_id,
                        "item": {
                            "type": "agentMessage",
                            "id": "msg-1",
                            "text": f"echo: {text}",
                        },
                    },
                }
            )
            await send(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "threadId": tid,
                        "turnId": turn_id,
                        "tokenUsage": {"total": {"totalTokens": 42}},
                    },
                }
            )
            await send(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": tid,
                        "turn": {"id": turn_id, "status": "completed"},
                    },
                }
            )
        elif m == "turn/interrupt":
            await send({"id": rid, "result": {}})
        else:
            await send(
                {"id": rid, "error": {"code": -32601, "message": f"unknown {m}"}}
            )

    async def close(self):
        self.server.close()
        await self.server.wait_closed()


async def wait_for(pred, timeout=8.0, step=0.05):  # noqa: ASYNC109
    for _ in range(int(timeout / step)):
        if await pred():
            return True
        await asyncio.sleep(step)
    return False


async def seed(db, fake):
    async with db() as s:
        repo = WorkbenchRepository(s)
        async with repo.transaction():
            env = await repo.register_environment(
                owner_actor_id=OWNER,
                name="pc",
                agent_version="0.1.0",
                codex_version="0.155.1",
                roots=["/home/u/research"],
                ceiling={"sandbox": "workspace-write", "network": False},
            )
            sb = await repo.register_sandbox(
                owner_actor_id=OWNER,
                app_server_url=f"ws://127.0.0.1:{fake.port}",
                token_ref=f"inline:{TOKEN}",
                codex_version="0.155.1",
            )
        sid = (
            await SessionService(repo).create(
                owner_actor_id=OWNER,
                environment_id=env,
                sandbox_id=sb,
                project_root="/home/u/research/proj",
            )
        )["session_id"]
        async with repo.transaction():
            await repo.enqueue_command(
                session_id=sid, sandbox_id=sb, kind="session.start_thread", payload={}
            )
        return env, sb, sid


def test_helpers():
    assert (
        event_kind("item/completed") == "item"
        and event_kind("thread/tokenUsage") == "usage"
        and event_kind("thread/environment/disconnected") == "environment"
        and event_kind("turn/completed") == "turn"
    )
    assert (
        thread_id_of({"threadId": "a"}) == "a"
        and thread_id_of({"thread": {"id": "b"}}) == "b"
        and thread_id_of({"turn": {"threadId": "c"}}) == "c"
        and thread_id_of({}) is None
    )


async def test_start_thread_and_user_turn_project_events(db):
    fake = FakeAppServer()
    await fake.start()
    try:
        _, sb, sid = await seed(db, fake)
        runner = Runner(
            db, publisher=Publisher(None, "t"), runner_id="r1", poll_seconds=0.05
        )
        assert await runner.run_once() == 1  # session.start_thread
        async with db() as s:
            sess = await WorkbenchRepository(s).get_session(sid)
        assert sess["thread_id"] and sess["thread_id"].startswith("thread-")
        assert fake.auth_headers[0] == f"Bearer {TOKEN}"
        assert [r["method"] for r in fake.requests][:2] == [
            "initialize",
            "thread/start",
        ]
        # 用户发 turn
        async with db() as s:
            repo = WorkbenchRepository(s)
            await SessionService(repo).record_user_turn_requested(
                sid, owner_actor_id=OWNER, text="hello", request_id="req-1"
            )
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid,
                    sandbox_id=sb,
                    kind="turn.start",
                    payload={"text": "hello", "request_id": "req-1", "by": "user"},
                )
        assert await runner.run_once() == 1

        async def turn_done():
            async with db() as s:
                return any(
                    e["type"] == "turn/completed"
                    for e in await WorkbenchRepository(s).list_events(session_id=sid)
                )

        assert await wait_for(turn_done)
        async with db() as s:
            events = await WorkbenchRepository(s).list_events(session_id=sid)
        types = [e["type"] for e in events]
        assert (
            "session/thread_started" in types
            and "turn/requested" in types
            and "turn/accepted" in types
        )
        assert (
            "item/completed" in types
            and "thread/tokenUsage/updated" in types
            and "turn/completed" in types
        )
        assert not any(t.endswith("/delta") for t in types)  # 流式 delta 不落库
        assert [e["cursor"] for e in events] == list(
            range(1, len(events) + 1)
        )  # 游标连续
        msg = next(e for e in events if e["type"] == "item/completed")
        assert msg["payload"]["item"]["text"] == "echo: hello" and msg["kind"] == "item"
        for link in runner.links.values():
            await link.client.close()
    finally:
        await fake.close()


async def test_user_wheel_approval_becomes_interaction_and_decision_returns(db):
    fake = FakeAppServer()
    await fake.start()
    try:
        _, sb, sid = await seed(db, fake)
        runner = Runner(
            db, publisher=Publisher(None, "t"), runner_id="r1", poll_seconds=0.05
        )
        await runner.run_once()
        async with db() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid,
                    sandbox_id=sb,
                    kind="turn.start",
                    payload={
                        "text": "please NEED_APPROVAL now",
                        "request_id": "req-2",
                        "by": "user",
                    },
                )
        await runner.run_once()

        async def opened():
            async with db() as s:
                return (
                    len(
                        await WorkbenchRepository(s).list_interactions(
                            session_id=sid, status="pending"
                        )
                    )
                    == 1
                )

        assert await wait_for(opened)
        async with db() as s:
            repo = WorkbenchRepository(s)
            it = (await repo.list_interactions(session_id=sid, status="pending"))[0]
            assert it["kind"] == "tool_approval" and it["task_id"] is None
            token = next(
                e["payload"]["token"]
                for e in await repo.list_events(session_id=sid)
                if e["type"] == "interaction/opened"
            )
            # 网页上的回答：消费 Interaction，再排一条 approval.respond 命令（与路由层做的一样）
            await Ledger(repo).respond_interaction(
                str(it["id"]),
                token=token,
                response={"decision": "accept"},
                owner_actor_id=OWNER,
            )
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid,
                    sandbox_id=sb,
                    kind="approval.respond",
                    payload={
                        "request_id": it["prompt"]["subject"]["request_id"],
                        "decision": "accept",
                    },
                )
        assert await runner.run_once() == 1

        async def decided():
            return len(fake.approval_decisions) == 1

        assert await wait_for(decided)
        assert fake.approval_decisions[0]["result"] == {"decision": "accept"}

        async def done():
            async with db() as s:
                return any(
                    e["type"] == "turn/completed"
                    for e in await WorkbenchRepository(s).list_events(session_id=sid)
                )

        assert await wait_for(done)
        async with db() as s:
            r = await s.execute(
                __import__("sqlalchemy").text(
                    "select decision, source from workbench_approval_log where session_id = :s"
                ),
                {"s": sid},
            )
            assert [tuple(x) for x in r.all()] == [("accept", "user")]
        for link in runner.links.values():
            await link.client.close()
    finally:
        await fake.close()


async def test_turn_refused_when_advisor_holds_wheel(db):
    from decimal import Decimal

    from app.domain.workbench.models import HandoverRequest

    fake = FakeAppServer()
    await fake.start()
    try:
        _, sb, sid = await seed(db, fake)
        runner = Runner(
            db, publisher=Publisher(None, "t"), runner_id="r1", poll_seconds=0.05
        )
        await runner.run_once()
        async with db() as s:
            repo = WorkbenchRepository(s)
            await Ledger(repo).handover(
                HandoverRequest(
                    idempotency_key="k-0000-0009",
                    session_id=sid,
                    profile_id="DATA_QUERY",
                    original_input={"text": "x"},
                    budget_limit=Decimal("5"),
                ),
                owner_actor_id=OWNER,
            )
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid,
                    sandbox_id=sb,
                    kind="turn.start",
                    payload={"text": "sneaky", "request_id": "req-3", "by": "user"},
                )
        await runner.run_once()
        async with db() as s:
            repo = WorkbenchRepository(s)
            events = await repo.list_events(session_id=sid)
            failed = [e for e in events if e["type"] == "command/failed"]
            assert (
                failed and "wheel is held by advisor" in failed[0]["payload"]["error"]
            )
            r = await s.execute(
                __import__("sqlalchemy").text(
                    "select status from workbench_commands where session_id = :s order by created_at"
                ),
                {"s": sid},
            )
            assert [x[0] for x in r.all()] == ["done", "failed"]
        assert not any(r["method"] == "turn/start" for r in fake.requests)
        for link in runner.links.values():
            await link.client.close()
    finally:
        await fake.close()


async def test_command_claims_are_exclusive(db):
    fake = FakeAppServer()
    await fake.start()
    try:
        _, sb, sid = await seed(db, fake)
        async with db() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                for i in range(20):
                    await repo.enqueue_command(
                        session_id=sid, sandbox_id=sb, kind="noop", payload={"i": i}
                    )

        async def claim(name):
            async with db() as s:
                repo = WorkbenchRepository(s)
                async with repo.transaction():
                    await repo.acquire_leases(runner_id=name, ttl_seconds=30)
                    return [
                        c["id"]
                        for c in await repo.claim_commands(claimed_by=name, limit=50)
                    ]

        # 分片：一个沙箱的命令只归持有其租约的 runner；另一个一条也拿不到
        a, b = await asyncio.gather(claim("r1"), claim("r2"))
        assert sorted([len(a), len(b)]) == [0, 21] and not set(a) & set(b)
        # 同一 runner 再拿：没有剩余
        assert await claim("r1") == [] and await claim("r2") == []
    finally:
        await fake.close()


async def test_lease_takeover_expires_pending_approvals_and_requeues_drives(db):
    """r1 崩了（租约过期）：r2 接管时作废 r1 留下的未决工具审批并发 interaction/expired，
    没有驾驶者的 Task 重新排 task.drive；r1 的旧租约再也拿不到命令。"""
    fake = FakeAppServer()
    await fake.start()
    try:
        _, sb, sid = await seed(db, fake)
        r1 = Runner(
            db, publisher=Publisher(None, "t"), runner_id="r1", poll_seconds=0.05
        )
        assert await r1.run_once() == 1
        async with db() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid,
                    sandbox_id=sb,
                    kind="turn.start",
                    payload={
                        "text": "please NEED_APPROVAL now",
                        "request_id": "req-x",
                        "by": "user",
                    },
                )
        await r1.run_once()

        async def opened():
            async with db() as s:
                return (
                    len(
                        await WorkbenchRepository(s).list_interactions(
                            session_id=sid, status="pending"
                        )
                    )
                    == 1
                )

        assert await wait_for(opened)
        # 模拟 r1 死掉：租约过期，链路不再服务
        async with db() as s:
            await s.execute(
                text(
                    "update workbench_sandbox_leases set expires_at = now() - interval '1 minute' where runner_id = 'r1'"
                )
            )
            await s.commit()
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid, sandbox_id=sb, kind="noop", payload={}
                )
        r2 = Runner(
            db, publisher=Publisher(None, "t"), runner_id="r2", poll_seconds=0.05
        )
        await r2.run_once()
        async with db() as s:
            repo = WorkbenchRepository(s)
            pending = await repo.list_interactions(session_id=sid, status="pending")
            expired = await repo.list_interactions(session_id=sid, status="expired")
            events = [
                e
                for e in await repo.list_events(session_id=sid)
                if e["type"] == "interaction/expired"
            ]
            leases = (
                (
                    await s.execute(
                        text(
                            "select runner_id from workbench_sandbox_leases where sandbox_id = :s"
                        ),
                        {"s": sb},
                    )
                )
                .scalars()
                .all()
            )
        assert pending == [] and len(expired) == 1 and len(events) == 1
        assert events[0]["payload"]["reason"] == "runner_restarted"
        assert leases == ["r2"]
        # r1 的旧租约拿不到新命令；r2 拿得到
        async with db() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid, sandbox_id=sb, kind="noop", payload={}
                )
                assert await repo.claim_commands(claimed_by="r1") == []
        assert await r2.run_once() >= 1
        for runner in (r1, r2):
            for link in runner.links.values():
                if link.client:
                    await link.client.close()
    finally:
        await fake.close()


async def test_takeover_requeues_running_tasks(db):
    fake = FakeAppServer()
    await fake.start()
    try:
        _, sb, sid = await seed(db, fake)
        from decimal import Decimal

        from app.domain.workbench.models import HandoverRequest
        from app.domain.workbench.states import TaskState

        async with db() as s:
            repo = WorkbenchRepository(s)
            led = Ledger(repo)
            task_id = (
                await led.handover(
                    HandoverRequest(
                        idempotency_key="takeover-0001",
                        session_id=sid,
                        profile_id="SMOKE",
                        original_input={"text": "q"},
                        budget_limit=Decimal("1"),
                    ),
                    owner_actor_id=OWNER,
                )
            )["task_id"]
            await led.transition(task_id, TaskState.VALIDATING)
            await led.transition(task_id, TaskState.QUEUED)
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid, sandbox_id=sb, kind="noop", payload={}
                )
        r2 = Runner(
            db, publisher=Publisher(None, "t"), runner_id="r2", poll_seconds=0.05
        )
        await r2.run_once()  # 首次拿租约 = 接管：QUEUED 的 Task 得到一条 task.drive
        async with db() as s:
            rows = (
                (
                    await s.execute(
                        text(
                            "select kind, payload, status from workbench_commands where kind = 'task.drive'"
                        )
                    )
                )
                .mappings()
                .all()
            )
        assert (
            len(rows) == 1
            and rows[0]["payload"]["task_id"] == task_id
            and rows[0]["payload"]["resumed"] is True
        )
        await r2.wait_drivers(timeout=5)
        for link in r2.links.values():
            if link.client:
                await link.client.close()
    finally:
        await fake.close()


@pytest.mark.parametrize("ref,expected", [("inline:abc", "abc")])
def test_token_ref(ref, expected):
    from app.infrastructure.workbench.app_server_client import resolve_token_ref

    assert resolve_token_ref(ref) == expected
    with pytest.raises(RuntimeError):
        resolve_token_ref("vault:nope")


async def test_mcp_elicitation_is_accepted_with_schema_defaults(db):
    """Codex 调 MCP 工具前的 user-verification 询问：表单式按 schema 默认值答 accept，工具调用得以完成。"""
    fake = FakeAppServer()
    await fake.start()
    try:
        _, sb, sid = await seed(db, fake)
        runner = Runner(
            db, publisher=Publisher(None, "t"), runner_id="r1", poll_seconds=0.05
        )
        assert await runner.run_once() == 1
        async with db() as s:
            repo = WorkbenchRepository(s)
            await SessionService(repo).record_user_turn_requested(
                sid, owner_actor_id=OWNER, text="NEED_ELICITATION", request_id="req-e"
            )
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid,
                    sandbox_id=sb,
                    kind="turn.start",
                    payload={
                        "text": "NEED_ELICITATION",
                        "request_id": "req-e",
                        "by": "user",
                    },
                )
        assert await runner.run_once() == 1

        async def turn_done():
            async with db() as s:
                return any(
                    e["type"] == "turn/completed"
                    for e in await WorkbenchRepository(s).list_events(session_id=sid)
                )

        assert await wait_for(turn_done)
        assert fake.elicitations == [
            {"action": "accept", "content": {"confirm": True, "note": "ok"}}
        ]
        async with db() as s:
            events = await WorkbenchRepository(s).list_events(session_id=sid)
        tool = next(
            e
            for e in events
            if e["type"] == "item/completed"
            and e["payload"]["item"].get("type") == "mcpToolCall"
        )
        assert tool["payload"]["item"]["status"] == "completed"
        for link in runner.links.values():
            await link.client.close()
    finally:
        await fake.close()


async def test_reconnect_reads_the_current_sandbox_token(db):
    """回收后重新拉起会换沙箱的能力令牌；runner 重连时要用库里的新令牌，而不是缓存里的旧令牌。"""
    fake = FakeAppServer()
    await fake.start()
    runner = Runner(
        db, publisher=Publisher(None, "t"), runner_id="r1", poll_seconds=0.05
    )
    try:
        _, sb, sid = await seed(db, fake)
        assert await runner.run_once() == 1  # session.start_thread，用旧令牌连上
        assert fake.auth_headers[-1] == f"Bearer {TOKEN}"
        # 沙箱被回收又拉起：连接断了，库里的令牌换了
        await runner.links[sb].client.close()
        async with db() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                await s.execute(
                    text(
                        "update workbench_sandboxes set token_ref = :r where id = :id"
                    ),
                    {"r": "inline:cap-token-after-reprovision", "id": sb},
                )
                await repo.enqueue_command(
                    session_id=sid,
                    sandbox_id=sb,
                    kind="turn.start",
                    payload={
                        "text": "hello again",
                        "request_id": "req-2",
                        "by": "user",
                    },
                )
        await runner.run_once()
        assert fake.auth_headers[-1] == "Bearer cap-token-after-reprovision"
    finally:
        for link in runner.links.values():
            if link.client is not None:
                await link.client.close()
        await fake.close()
