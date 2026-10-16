"""作为运行方跑分身测试（SDD 0027 §3.4）：真数据库、真 runner、假 app-server 按脚本回复。

受理 → 推进器在测试电脑的固定项目里开对话、用草稿快照建委托 → runner 驱动 → 收结果。
有人值守：开步前的提问没有预给答案就报成待回答的问题，knowlab 交来答案后代测试账号回答，接着跑。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from knowbot_fixtures import manifest
from sqlalchemy import text
from test_workbench_advisor_db import ANSWER, PLAN, ScriptedAppServer, close, drive
from test_workbench_ledger_db import OWNER
from test_workbench_ledger_db import db as db

from app.application.workbench.test_runs import (
    KnowbotTestConductor,
    KnowbotTestConfig,
    KnowbotTestService,
)
from app.bootstrap.workbench import build_runner, workbench_stores
from app.domain.workbench.errors import (
    KnowbotInvalid,
    KnowbotTestBudgetExhausted,
    KnowbotTestConflict,
    KnowbotTestUnconfigured,
    QuestionNotOpen,
)
from app.domain.workbench.test_runs import Answers, RunRequest
from app.infrastructure.workbench.publisher import RedisPublisher
from app.infrastructure.workbench.repository import WorkbenchRepository

WORKSPACE = "/workspace"
TESTER = "knowbot-tester"
CONFIG = KnowbotTestConfig(subject=TESTER, workspace_root=WORKSPACE)
ASK = [
    {
        "id": "period",
        "question": "看哪几年？",
        "options": [{"id": "3y", "label": "近三年"}],
    }
]


class Clock:
    def __init__(self) -> None:
        self.at = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.at


def request(run_id: str, *, doc=None, answers=None, expect="README", budget="10"):
    return RunRequest.model_validate(
        {
            "contract": "knowbot-test-run/v1",
            "run_id": run_id,
            "owner_subject": "user-123",
            "manifest": doc or manifest(),
            "cases": [
                {
                    "case_id": "c1",
                    "input": {
                        "question": "这个项目里有什么？",
                        "answers": answers or {},
                    },
                    "expectations": [
                        {
                            "artifact": "answer",
                            "path": "answer",
                            "kind": "contains",
                            "value": expect,
                        }
                    ],
                }
            ],
            "budget": {"limit": budget},
            "timeout_seconds": 3600,
            "max_wait_seconds": 600,
        }
    )


def asking() -> dict:
    doc = manifest()
    doc["pack"]["workflow"][0]["ask_before"] = ASK
    return doc


async def setup_test_computer(db, fake) -> None:
    """测试账号登录过、测试电脑已配对（登记了工作区根目录）、有自己的沙箱。"""
    async with db() as s:
        await s.execute(
            text(
                """insert into auth_user (id, issuer, subject, roles, scopes)
                   values (:i, 'https://casdoor.test', :s, '[]', '[]')"""
            ),
            {"i": OWNER, "s": TESTER},
        )
        await s.commit()
        repo = WorkbenchRepository(s)
        async with repo.transaction():
            await repo.register_environment(
                owner_actor_id=OWNER,
                name="knowbot-test",
                agent_version="0.4.1",
                codex_version="0.155.1",
                roots=[WORKSPACE],
                ceiling={},
            )
            await repo.register_sandbox(
                owner_actor_id=OWNER,
                app_server_url=f"ws://127.0.0.1:{fake.port}",
                token_ref="inline:t",
                codex_version="0.155.1",
            )


def runner_for(db):
    return build_runner(
        db, publisher=RedisPublisher(None, "t"), runner_id="r", poll_seconds=0.05
    )


async def accept(db, req, config=CONFIG):
    async with db() as s:
        return await KnowbotTestService(WorkbenchRepository(s), config).accept(
            req, caller="knowlab"
        )


async def status(db, run_id):
    async with db() as s:
        return await KnowbotTestService(WorkbenchRepository(s), CONFIG).status(run_id)


async def answer(db, run_id, body):
    async with db() as s:
        return await KnowbotTestService(WorkbenchRepository(s), CONFIG).answer(
            run_id, Answers.model_validate({"contract": "knowbot-test-run/v1", **body})
        )


async def test_a_draft_runs_on_the_real_engine_and_passes(db):
    fake = ScriptedAppServer([PLAN, ANSWER])
    await fake.start()
    runner = runner_for(db)
    try:
        await setup_test_computer(db, fake)
        accepted = await accept(db, request("knowlab-run-0001"))
        assert accepted["status"] == "accepted" and accepted["cases"] == []
        conductor = KnowbotTestConductor(workbench_stores(db), CONFIG)
        await conductor.tick()  # 开对话、建委托、排驾驶
        await drive(runner)
        await conductor.tick()  # 收这条用例的结果
        await conductor.tick()  # 全部用例都有结果：收尾
        done = await status(db, "knowlab-run-0001")
        assert done["status"] == "passed", done
        [case] = done["cases"]
        assert case["status"] == "passed"
        assert [s["step_id"] for s in case["steps"]] == ["plan", "answer"]
        assert all(s["accepted"] for s in case["steps"])
        assert case["expectations"] == [True]
        # 不回交回物的内容（C-L5）：模型写的回答原文不出现在结果里
        assert "The project has one README" not in json.dumps(done, ensure_ascii=False)
        assert Decimal(done["cost"]["spent"]) > 0
        async with db() as s:
            task = (
                await s.execute(
                    text(
                        "select pack_source, profile_id, owner_actor_id from workbench_tasks"
                    )
                )
            ).one()
        assert task.pack_source == "knowlab-test"
        assert task.profile_id.startswith("knowlab-test:")
        assert str(task.owner_actor_id) == OWNER  # 跑在测试账号上
    finally:
        await close(runner)
        await fake.close()


async def test_unmet_expectations_fail_the_case(db):
    fake = ScriptedAppServer([PLAN, ANSWER])
    await fake.start()
    runner = runner_for(db)
    try:
        await setup_test_computer(db, fake)
        await accept(db, request("knowlab-run-0002", expect="不存在的说法"))
        conductor = KnowbotTestConductor(workbench_stores(db), CONFIG)
        await conductor.tick()
        await drive(runner)
        await conductor.tick()
        await conductor.tick()
        done = await status(db, "knowlab-run-0002")
        assert done["status"] == "failed"
        assert done["cases"][0]["status"] == "failed"
        assert done["cases"][0]["expectations"] == [False]
    finally:
        await close(runner)
        await fake.close()


async def test_an_unanswered_ask_before_waits_for_knowlab_then_continues(db):
    fake = ScriptedAppServer([PLAN, ANSWER])
    await fake.start()
    runner = runner_for(db)
    try:
        await setup_test_computer(db, fake)
        await accept(db, request("knowlab-run-0003", doc=asking()))
        conductor = KnowbotTestConductor(workbench_stores(db), CONFIG)
        await conductor.tick()
        await drive(runner)
        waiting = await status(db, "knowlab-run-0003")
        assert waiting["status"] == "waiting", waiting
        [q] = waiting["questions"]
        assert q["kind"] == "ask_before" and q["key"] == "period"
        assert q["case_id"] == "c1" and q["options"] == [
            {"id": "3y", "label": "近三年"}
        ]
        with pytest.raises(QuestionNotOpen):
            await answer(
                db,
                "knowlab-run-0003",
                {
                    "answers": [
                        {"question_id": "nope", "text": "x", "answered_by": "user"}
                    ]
                },
            )
        body = {
            "answers": [
                {
                    "question_id": q["question_id"],
                    "option_id": "3y",
                    "answered_by": "user",
                }
            ]
        }
        await answer(db, "knowlab-run-0003", body)
        await answer(db, "knowlab-run-0003", body)  # 同样的答案重复交：原样
        with pytest.raises(KnowbotTestConflict):
            await answer(
                db,
                "knowlab-run-0003",
                {
                    "answers": [
                        {
                            "question_id": q["question_id"],
                            "text": "近五年",
                            "answered_by": "user",
                        }
                    ]
                },
            )
        await conductor.tick()  # 代测试账号回答，委托回到排队
        await drive(runner)
        await conductor.tick()
        await conductor.tick()
        done = await status(db, "knowlab-run-0003")
        assert done["status"] == "passed", done
        assert any("近三年" in t for t in fake.turn_inputs)  # 答案进了那一步的输入
    finally:
        await close(runner)
        await fake.close()


async def test_preset_answers_are_used_without_asking(db):
    fake = ScriptedAppServer([PLAN, ANSWER])
    await fake.start()
    runner = runner_for(db)
    try:
        await setup_test_computer(db, fake)
        await accept(
            db, request("knowlab-run-0004", doc=asking(), answers={"period": "3y"})
        )
        conductor = KnowbotTestConductor(workbench_stores(db), CONFIG)
        await conductor.tick()
        await drive(runner)
        assert (await status(db, "knowlab-run-0004"))["questions"] == []
        await conductor.tick()  # 用用例里预给的答案代答
        await drive(runner)
        await conductor.tick()
        await conductor.tick()
        assert (await status(db, "knowlab-run-0004"))["status"] == "passed"
        assert any("近三年" in t for t in fake.turn_inputs)
    finally:
        await close(runner)
        await fake.close()


async def test_a_question_nobody_answers_ends_as_needs_human(db):
    fake = ScriptedAppServer([PLAN, ANSWER])
    await fake.start()
    runner = runner_for(db)
    clock = Clock()
    try:
        await setup_test_computer(db, fake)
        await accept(db, request("knowlab-run-0005", doc=asking()))
        conductor = KnowbotTestConductor(workbench_stores(db), CONFIG, now=clock)
        await conductor.tick()
        await drive(runner)
        await conductor.tick()  # 开始计等待
        clock.at += timedelta(seconds=700)  # 超过最长等待（600 秒）
        await conductor.tick()
        await conductor.tick()
        done = await status(db, "knowlab-run-0005")
        assert done["status"] == "failed"
        assert done["cases"][0]["status"] == "needs_human"
        assert done["waited_seconds"] >= 700  # 等人的时间单独记，不算进超时
    finally:
        await close(runner)
        await fake.close()


async def test_acceptance_refusals_and_conflicts(db):
    fake = ScriptedAppServer([])
    await fake.start()
    try:
        with pytest.raises(KnowbotTestUnconfigured):
            await accept(db, request("knowlab-run-0010"), KnowbotTestConfig(None, None))
        await setup_test_computer(db, fake)
        bad = manifest()
        bad["pack"]["workflow"][0]["tools"] = ["run_sql"]  # 数据线工具（C-L2）
        with pytest.raises(KnowbotInvalid):
            await accept(db, request("knowlab-run-0011", doc=bad))
        await accept(db, request("knowlab-run-0012"))
        again = await accept(db, request("knowlab-run-0012"))
        assert again["run_id"] == "knowlab-run-0012"
        with pytest.raises(KnowbotTestConflict):
            await accept(db, request("knowlab-run-0012", budget="11"))
        # 每月上限 1000：在跑的按上限占用
        await accept(db, request("knowlab-run-0013", budget="980"))
        with pytest.raises(KnowbotTestBudgetExhausted):
            await accept(db, request("knowlab-run-0014", budget="20"))
    finally:
        await fake.close()


async def test_timeouts_and_seven_day_cleanup(db):
    fake = ScriptedAppServer([PLAN])  # 第二步一直没有合格回复
    await fake.start()
    runner = runner_for(db)
    clock = Clock()
    try:
        await setup_test_computer(db, fake)
        await accept(db, request("knowlab-run-0020"))
        conductor = KnowbotTestConductor(workbench_stores(db), CONFIG, now=clock)
        await conductor.tick()
        clock.at += timedelta(seconds=3601)
        await conductor.tick()
        done = await status(db, "knowlab-run-0020")
        assert done["status"] == "timeout"
        assert done["cases"][0]["status"] == "error"
        async with db() as s:
            sessions = (
                await s.execute(text("select count(*) from workbench_sessions"))
            ).scalar_one()
        assert sessions == 1
        clock.at += timedelta(days=7, seconds=1)
        assert await conductor.cleanup() == 1
        async with db() as s:
            left = (
                await s.execute(text("select count(*) from workbench_sessions"))
            ).scalar_one()
            tasks = (
                await s.execute(text("select count(*) from workbench_tasks"))
            ).scalar_one()
        assert (left, tasks) == (0, 0)
        assert (await status(db, "knowlab-run-0020"))["status"] == "timeout"  # 结果留着
    finally:
        await close(runner)
        await fake.close()


@pytest.fixture
def unique_run():
    return f"knowlab-run-{uuid.uuid4().hex[:8]}"


async def test_a_mid_step_approval_is_answered_through_knowlab(db):
    """中途的工具审批（runner 级的等待）：报成 tool_approval 问题，运营批准后答复送到 app-server。"""
    import asyncio

    from test_workbench_runner_db import FakeAppServer, wait_for

    fake = FakeAppServer()
    await fake.start()
    runner = runner_for(db)
    running = asyncio.create_task(runner.run_forever())
    try:
        await setup_test_computer(db, fake)
        req = request("knowlab-run-0030")
        body = req.model_dump(mode="json")
        body["cases"][0]["input"]["question"] = "NEED_APPROVAL 看看项目"
        await accept(db, RunRequest.model_validate(body))
        conductor = KnowbotTestConductor(workbench_stores(db), CONFIG)

        async def asked():
            await conductor.tick()
            current = await status(db, "knowlab-run-0030")
            return current["status"] == "waiting"

        assert await wait_for(asked, timeout=15)
        [q] = (await status(db, "knowlab-run-0030"))["questions"]
        assert q["kind"] == "tool_approval" and q["allow_free_text"] is False
        assert "touch /home/u/x" in q["text"]
        await answer(
            db,
            "knowlab-run-0030",
            {
                "answers": [
                    {
                        "question_id": q["question_id"],
                        "decision": "approve",
                        "answered_by": "operator",
                    }
                ]
            },
        )

        async def approved():
            await conductor.tick()
            return any(
                (d.get("result") or {}).get("decision") == "accept"
                for d in fake.approval_decisions
            )

        assert await wait_for(approved, timeout=15)
        async with db() as s:
            rows = (
                await s.execute(
                    text("select decision, source from workbench_approval_log")
                )
            ).all()
        assert ("accept", "user") in [tuple(r) for r in rows]  # 代测试账号批准的
    finally:
        runner.stop()
        await asyncio.wait_for(running, 10)
        # 还在驾驶的那一步握着数据库连接：停掉它，免得夹具收尾删 schema 时等锁
        drivers = list(runner.driving.values())
        for task in drivers:
            task.cancel()
        await asyncio.gather(*drivers, return_exceptions=True)
        await close(runner)
        await fake.close()
