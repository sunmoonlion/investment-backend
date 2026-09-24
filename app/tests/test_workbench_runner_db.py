"""runner 与 app-server 客户端：假 app-server（进程内 WebSocket）+ 真数据库。
验：thread/start 落 thread_id、turn 命令、通知投影成事件（delta 不落）、用户自驾时审批变 Interaction 并把决定回给 app-server、命令认领互斥。"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
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
                    return [
                        c["id"]
                        for c in await repo.claim_commands(claimed_by=name, limit=50)
                    ]

        a, b = await asyncio.gather(claim("r1"), claim("r2"))
        assert len(a) + len(b) == 21 and not set(a) & set(b)
    finally:
        await fake.close()


@pytest.mark.parametrize("ref,expected", [("inline:abc", "abc")])
def test_token_ref(ref, expected):
    from app.infrastructure.workbench.app_server_client import resolve_token_ref

    assert resolve_token_ref(ref) == expected
    with pytest.raises(RuntimeError):
        resolve_token_ref("vault:nope")
