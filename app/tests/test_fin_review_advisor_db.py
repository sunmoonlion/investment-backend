"""财报体检专家包在顾问上跑：假 app-server 按脚本回复，真数据库（0008-info 段四，MVP-07）。

验的是去向：顺利走完七步；公司未入库、勾稽不平、与官方对不上时停下交人，而且不返工；
取数不合格退回数据摸底。
"""

from __future__ import annotations

import json

from test_fin_review_pack import (
    CROSSCHECK,
    FACTS,
    METRICS,
    NOTE,
    PROFILE,
    RECONCILE,
    SCOPE,
)
from test_workbench_advisor_db import ScriptedAppServer, close, drive, seed
from test_workbench_ledger_db import OWNER
from test_workbench_ledger_db import db as db

from app.application.workbench.ledger import Ledger
from app.domain.workbench.states import TaskState
from app.infrastructure.workbench.repository import WorkbenchRepository


def say(content) -> str:
    return json.dumps(content, ensure_ascii=False)


ALL = [SCOPE, PROFILE, RECONCILE, FACTS, METRICS, CROSSCHECK, NOTE]


async def run(db, replies):
    fake = ScriptedAppServer([say(r) if not isinstance(r, str) else r for r in replies])
    await fake.start()
    runner, sid, sb, task_id = await seed(db, fake, profile="FIN_REVIEW")
    await drive(runner)
    return fake, runner, sid, sb, task_id


async def attempts_of(db, task_id):
    async with db() as s:
        rows = await WorkbenchRepository(s).list_attempts(task_id)
    return [(a["step_id"], a["status"], a["failure_code"]) for a in rows]


async def waiting_on(db, sid, task_id):
    async with db() as s:
        repo = WorkbenchRepository(s)
        task = await repo.get_task(task_id)
        assert task["state"] == TaskState.WAITING, task["state"]
        assert task["waiting_reason"] == "INPUT"
        (pending,) = await repo.list_interactions(session_id=sid, status="pending")
        return pending["prompt"]["subject"]


async def test_seven_steps_end_with_the_working_paper(db):
    fake, runner, sid, _, task_id = await run(db, ALL)
    try:
        async with db() as s:
            repo = WorkbenchRepository(s)
            task = await repo.get_task(task_id)
            assert task["state"] == TaskState.SUCCEEDED and task["current_step"] == 7
            assert task["acceptance_contract"] == {
                "pack_id": "FIN_REVIEW",
                "steps": [
                    "scope",
                    "profile",
                    "reconcile",
                    "extract",
                    "metrics",
                    "crosscheck",
                    "note",
                ],
            }
            names = {
                (a["name"], a["version"]) for a in await repo.list_artifacts(task_id)
            }
            assert names == {
                ("scope", 1),
                ("profile", 1),
                ("reconcile", 1),
                ("facts", 1),
                ("metrics", 1),
                ("crosscheck", 1),
                ("note", 1),
                ("handback", 1),
            }
            note = await repo.get_artifact(task_id=task_id, name="note")
            assert note["content"]["conclusion"] == ""
            assert str(task["terminal_result_ref"]) == str(note["id"])
            assert (await repo.get_session(sid))["wheel"] == "user"
        assert len(fake.turn_inputs) == 7
        assert "list_datasets" in fake.turn_inputs[1]
        assert "### scope (v1)" in fake.turn_inputs[1]
        # 勾稽这一步只拿到范围与摸底，拿不到也不需要后面的东西
        assert "### profile (v1)" in fake.turn_inputs[2]
        assert "### facts" not in fake.turn_inputs[2]
        # 成稿拿到前面全部六份交回物
        for name in ("scope", "profile", "reconcile", "facts", "metrics", "crosscheck"):
            assert f"### {name} (v1)" in fake.turn_inputs[6], name
    finally:
        await close(runner)
        await fake.close()


async def test_a_company_that_was_not_ingested_is_handed_to_the_user(db):
    missing = PROFILE | {
        "dataset": None,
        "not_ingested": True,
        "data_version": "",
        "periods": [],
    }
    fake, runner, sid, _, task_id = await run(db, [SCOPE, missing, missing, RECONCILE])
    try:
        subject = await waiting_on(db, sid, task_id)
        assert subject["step_id"] == "profile"
        assert (
            "non_empty(dataset): 这家公司未入库：没有对应的数据集"
            in subject["failures"]
        )
        assert await attempts_of(db, task_id) == [
            ("scope", "COMPLETED", None),
            ("profile", "FAILED", "acceptance"),
            ("profile", "FAILED", "acceptance"),
        ]
        assert len(fake.turn_inputs) == 3  # 没有往下走
    finally:
        await close(runner)
        await fake.close()


async def test_unbalanced_books_stop_the_review_without_a_second_try(db):
    checks = [dict(c) for c in RECONCILE["checks"]]
    checks[0]["unbalanced"] = 2
    unbalanced = RECONCILE | {"checks": checks}
    fake, runner, sid, sb, task_id = await run(
        db, [SCOPE, PROFILE, unbalanced, RECONCILE, *ALL[3:]]
    )
    try:
        subject = await waiting_on(db, sid, task_id)
        assert subject["step_id"] == "reconcile"
        assert subject["failures"] == ["all_equal(checks): 有勾稽规则不平，不往下算"]
        assert '"unbalanced": 2' in subject["last_output"]  # 用户看得到原样的结果
        assert await attempts_of(db, task_id) == [
            ("scope", "COMPLETED", None),
            ("profile", "COMPLETED", None),
            ("reconcile", "FAILED", "acceptance"),
        ]
        assert len(fake.turn_inputs) == 3
        assert not any("rework" in text for text in fake.turn_inputs)
        async with db() as s:
            repo = WorkbenchRepository(s)
            assert await repo.get_artifact(task_id=task_id, name="reconcile") is None
            (pending,) = await repo.list_interactions(session_id=sid, status="pending")
            token = next(
                e["payload"]["token"]
                for e in await repo.list_events(session_id=sid)
                if e["type"] == "interaction/opened"
            )
            result = await Ledger(repo).respond_interaction(
                str(pending["id"]),
                token=token,
                response={"decision": "stop"},
                owner_actor_id=OWNER,
            )
            assert result["state"] == TaskState.FAILED
            assert (await repo.get_session(sid))["wheel"] == "user"
    finally:
        await close(runner)
        await fake.close()


async def test_the_user_may_ask_for_the_reconciliation_to_be_run_again(db):
    checks = [dict(c) for c in RECONCILE["checks"]]
    checks[0]["unbalanced"] = 2
    fake, runner, sid, sb, task_id = await run(
        db, [SCOPE, PROFILE, RECONCILE | {"checks": checks}, RECONCILE, *ALL[3:]]
    )
    try:
        await waiting_on(db, sid, task_id)
        async with db() as s:
            repo = WorkbenchRepository(s)
            (pending,) = await repo.list_interactions(session_id=sid, status="pending")
            token = next(
                e["payload"]["token"]
                for e in await repo.list_events(session_id=sid)
                if e["type"] == "interaction/opened"
            )
            await Ledger(repo).respond_interaction(
                str(pending["id"]),
                token=token,
                response={"decision": "rework"},
                owner_actor_id=OWNER,
            )
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid,
                    sandbox_id=sb,
                    kind="task.drive",
                    payload={"task_id": task_id},
                )
        await drive(runner)
        async with db() as s:
            task = await WorkbenchRepository(s).get_task(task_id)
            assert task["state"] == TaskState.SUCCEEDED
        assert [a[0] for a in await attempts_of(db, task_id)] == [
            "scope",
            "profile",
            "reconcile",
            "reconcile",
            "extract",
            "metrics",
            "crosscheck",
            "note",
        ]
    finally:
        await close(runner)
        await fake.close()


async def test_a_figure_that_disagrees_with_the_report_stops_before_the_note(db):
    mismatch = CROSSCHECK | {
        "mismatched": [
            {
                "fiscal_year": 2025,
                "item": "operate_income",
                "basis": "原始披露",
                "official": 2.0,
                "dataset": 1.0,
                "source_report": "2025年年度报告",
                "page": 6,
            }
        ]
    }
    fake, runner, sid, _, task_id = await run(
        db, [*ALL[:5], mismatch, CROSSCHECK, NOTE]
    )
    try:
        subject = await waiting_on(db, sid, task_id)
        assert subject["step_id"] == "crosscheck"
        assert subject["failures"] == [
            "list_empty(mismatched): 有数字与公司披露的原文对不上，不往下写"
        ]
        assert len(fake.turn_inputs) == 6
        async with db() as s:
            repo = WorkbenchRepository(s)
            assert await repo.get_artifact(task_id=task_id, name="note") is None
            assert await repo.get_artifact(task_id=task_id, name="metrics") is not None
    finally:
        await close(runner)
        await fake.close()


async def test_a_failed_extraction_goes_back_to_the_data_profile(db):
    empty = FACTS | {"table": []}
    fake, runner, sid, _, task_id = await run(
        db, [SCOPE, PROFILE, RECONCILE, empty, empty, PROFILE, RECONCILE, *ALL[3:]]
    )
    try:
        async with db() as s:
            repo = WorkbenchRepository(s)
            task = await repo.get_task(task_id)
            assert task["state"] == TaskState.SUCCEEDED
            assert (await repo.get_artifact(task_id=task_id, name="profile"))[
                "version"
            ] == 2
            assert (await repo.get_artifact(task_id=task_id, name="facts"))[
                "version"
            ] == 1
        assert [a[:2] for a in await attempts_of(db, task_id)] == [
            ("scope", "COMPLETED"),
            ("profile", "COMPLETED"),
            ("reconcile", "COMPLETED"),
            ("extract", "FAILED"),
            ("extract", "FAILED"),
            ("profile", "COMPLETED"),
            ("reconcile", "COMPLETED"),
            ("extract", "COMPLETED"),
            ("metrics", "COMPLETED"),
            ("crosscheck", "COMPLETED"),
            ("note", "COMPLETED"),
        ]
        # 退回之后，后面的步骤用的是新版本的摸底结果
        assert "### profile (v2)" in fake.turn_inputs[7]
    finally:
        await close(runner)
        await fake.close()


async def test_a_note_with_advice_or_a_conclusion_is_not_handed_back_as_done(db):
    advice = NOTE | {"observations": ["综合来看建议买入"]}
    concluded = NOTE | {"conclusion": "基本面改善"}
    fake, runner, sid, _, task_id = await run(db, [*ALL[:6], advice, concluded])
    try:
        subject = await waiting_on(db, sid, task_id)
        assert subject["step_id"] == "note"
        assert subject["failures"] == ["blank(conclusion): 结论栏必须留空"]
        assert (await attempts_of(db, task_id))[-2:] == [
            ("note", "FAILED", "acceptance"),
            ("note", "FAILED", "acceptance"),
        ]
        async with db() as s:
            repo = WorkbenchRepository(s)
            assert await repo.get_artifact(task_id=task_id, name="note") is None
            rejected = [
                e["payload"]["failures"]
                for e in await repo.list_events(session_id=sid)
                if e["type"] == "step/rejected"
            ]
        assert rejected[0] == [
            "no_positioning_advice: 含有评级、目标价或买卖建议的措辞"
        ]
    finally:
        await close(runner)
        await fake.close()
