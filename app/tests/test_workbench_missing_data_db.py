"""查一家没有数据的公司：对话里记一条「没有数据」，同一对话同一家只记一次（AT-INV-11）。"""

from __future__ import annotations

from test_workbench_ledger_db import db as db
from test_workbench_modes_db import closing, open_conversation, say, setup
from test_workbench_runner_db import FakeAppServer

from app.bootstrap.workbench import build_runner
from app.infrastructure.workbench.publisher import RedisPublisher
from app.infrastructure.workbench.repository import WorkbenchRepository


async def noted(db, sid):
    async with db() as s:
        events = await WorkbenchRepository(s).list_events(session_id=sid)
    return [e["payload"] for e in events if e["type"] == "data.missing"]


async def test_once_per_conversation_and_company(db):
    fake = FakeAppServer()
    await fake.start()
    try:
        _, sandbox, _ = await setup(db, fake)
        runner = build_runner(
            db, publisher=RedisPublisher(None, "t"), runner_id="r1", poll_seconds=0.05
        )
        first = await open_conversation(db, sandbox, "chat")
        second = await open_conversation(db, sandbox, "chat")
        assert await runner.run_once() == 2

        await say(db, runner, sandbox, first, "QUERY sh600519-financials")
        assert await noted(db, first) == [
            {
                "security_code": "600519",
                "market": "sh",
                "dataset": "sh600519-financials",
                "source": "tool",
            }
        ]
        # 同一段对话再查同一家（数据集名不同也算同一家）：不再记第二条
        await say(
            db, runner, sandbox, first, "QUERY sh600519-financials sh600519-prices"
        )
        assert len(await noted(db, first)) == 1
        # 查另一家：另记一条
        await say(db, runner, sandbox, first, "QUERY sz000001-financials")
        assert [n["security_code"] for n in await noted(db, first)] == [
            "600519",
            "000001",
        ]
        # 认不出代码的：不记，对话里照常显示工具的答复
        await say(db, runner, sandbox, first, "QUERY retail-v2")
        assert len(await noted(db, first)) == 2
        async with db() as s:
            events = await WorkbenchRepository(s).list_events(session_id=first)
        calls = [
            e
            for e in events
            if e["type"] == "item/completed"
            and e["payload"]["item"].get("type") == "mcpToolCall"
        ]
        assert len(calls) == 5

        # 另一段对话查同一家：那段对话自己记一条
        await say(db, runner, sandbox, second, "QUERY sh600519-financials")
        assert [n["security_code"] for n in await noted(db, second)] == ["600519"]
        assert len(await noted(db, first)) == 2
        await closing(runner)
    finally:
        await fake.close()
