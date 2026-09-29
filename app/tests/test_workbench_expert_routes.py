"""专家这一面的接口（PRD/apps/investment.md 7.3；AT-INV-08、15、19）：真数据库，依赖覆盖注入身份。"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from test_workbench_ledger_db import db as db  # noqa: F401
from test_workbench_project_routes import machine, project
from test_workbench_routes import A, B
from test_workbench_routes import make_client as make_client  # noqa: F401

QUESTION = "恒瑞医药 2023 到 2025 年的盈利能力怎么样？"


def ask(key=None, **more):
    return {
        "idempotency_key": key or f"k-{uuid.uuid4().hex[:12]}",
        "expert": "FIN_REVIEW",
        "question": QUESTION,
        "budget_limit": "2",
        **more,
    }


async def count(db, table):  # noqa: F811
    async with db() as s:
        return (await s.execute(text(f"select count(*) from {table}"))).scalar_one()


async def ready(http):
    env = await machine(http)
    return env, (await project(http, env)).json()["id"]


async def test_asking_an_expert_from_the_expert_entry(make_client, db):  # noqa: F811
    async with make_client(A) as http:
        _, pid = await ready(http)
        got = await http.post(f"/api/workbench/projects/{pid}/delegations", json=ask())
        assert got.status_code == 201, got.text
        made = got.json()
        assert made["created"] is True and made["state"] == "RECEIVED"
        assert made["project_id"] == pid

        talk = (await http.get(f"/api/workbench/sessions/{made['session_id']}")).json()[
            "session"
        ]
        assert talk["kind"] == "work" and talk["project_id"] == pid
        assert talk["wheel"] == "advisor"
        assert talk["active_task_id"] == made["task_id"]
        assert talk["title"] == "恒瑞医药 2023 到 2025 年的盈利能力怎么样？"

        shown = (await http.get(f"/api/workbench/tasks/{made['task_id']}/steps")).json()
        assert shown["contract_version"] == 2
        assert shown["task"]["question"] == QUESTION
        assert shown["task"]["budget"] == {
            "currency": "CNY",
            "limit": "2.00",
            "used": "0.00",
            "reserved": "0.00",
            "left": "2.00",
        }
        assert shown["position"] == {"step": 1, "of": 7, "title": "定范围", "left": 7}
        assert [s["status"] for s in shown["steps"]] == ["running"] + ["pending"] * 6
        one = await http.get(f"/api/workbench/tasks/{made['task_id']}/steps/2")
        assert one.status_code == 200
        assert one.json()["step"]["attempts"] == []
        assert "method" not in got.text + one.text

    async with db() as s:
        (command,) = (
            await s.execute(
                text("select kind, payload from workbench_commands order by created_at")
            )
        ).all()
    assert command[0] == "task.drive"
    assert command[1] == {"task_id": made["task_id"]}


async def test_the_same_request_sent_twice_makes_one_conversation(make_client, db):  # noqa: F811
    async with make_client(A) as http:
        _, pid = await ready(http)
        body = ask("k-same-request-1")
        first = await http.post(f"/api/workbench/projects/{pid}/delegations", json=body)
        again = await http.post(f"/api/workbench/projects/{pid}/delegations", json=body)
        assert first.status_code == again.status_code == 201
        assert again.json() == first.json() | {"created": False}
        other = await http.post(
            f"/api/workbench/projects/{pid}/delegations",
            json=body | {"question": "另一个问题"},
        )
        assert other.status_code == 409
        assert other.json()["code"] == "idempotency_conflict"
    assert await count(db, "workbench_sessions") == 1
    assert await count(db, "workbench_tasks") == 1
    assert await count(db, "workbench_commands") == 1


async def test_nothing_is_left_behind_when_the_handover_fails(make_client, db):  # noqa: F811
    """AT-INV-08：这个项目里专家已经在做一件事，第二次请专家交不出去，也不留下空的对话。"""
    async with make_client(A) as http:
        _, pid = await ready(http)
        first = await http.post(
            f"/api/workbench/projects/{pid}/delegations", json=ask()
        )
        assert first.status_code == 201
        before = await count(db, "workbench_sessions")
        events = await count(db, "workbench_session_events")
        second = await http.post(
            f"/api/workbench/projects/{pid}/delegations", json=ask()
        )
        assert second.status_code == 409
        assert second.json()["code"] == "project_busy"
    assert await count(db, "workbench_sessions") == before == 1
    assert await count(db, "workbench_session_events") == events
    assert await count(db, "workbench_tasks") == 1
    assert await count(db, "workbench_commands") == 1


async def test_a_machine_that_is_offline(make_client, db):  # noqa: F811
    """AT-INV-14 的后端一半"""
    async with make_client(A) as http:
        env, pid = await ready(http)
        async with db() as s:
            await s.execute(
                text(
                    "update workbench_environments set status = 'offline' where id = :e"
                ),
                {"e": env},
            )
            await s.commit()
        got = await http.post(f"/api/workbench/projects/{pid}/delegations", json=ask())
        assert got.status_code == 409
        assert got.json()["code"] == "environment_offline"
    assert await count(db, "workbench_sessions") == 0
    assert await count(db, "workbench_tasks") == 0


async def test_what_is_refused(make_client, db):  # noqa: F811
    async with make_client(A) as http:
        env = await machine(http, sandbox=False)
        pid = (await project(http, env)).json()["id"]
        url = f"/api/workbench/projects/{pid}/delegations"
        got = await http.post(url, json=ask())
        assert (got.status_code, got.json()["code"]) == (409, "no_sandbox")

    async with make_client(A) as http:
        await http.post(
            "/api/workbench/sandboxes",
            json={"app_server_url": "ws://sandbox:47800", "token_ref": "env:T"},
        )
        for body, status, code in (
            (ask(expert="SMOKE"), 404, "no_such_expert"),  # 只给我们自己用的
            (ask(expert="NOPE"), 404, "no_such_expert"),
            (ask(question="   "), 400, "workbench_error"),
        ):
            got = await http.post(url, json=body)
            assert (got.status_code, got.json()["code"]) == (status, code), body
        for body in (
            ask(budget_limit="0"),
            ask(budget_currency="USD"),
            ask(question=""),
            # 页面伪造设置：一概不收
            ask(thread_settings={"sandbox": "danger-full-access"}),
            ask(project_root="/etc"),
        ):
            assert (await http.post(url, json=body)).status_code == 422, body
        missing = f"/api/workbench/projects/{uuid.uuid4()}/delegations"
        assert (await http.post(missing, json=ask())).status_code == 404

        archived = (await project(http, env, "old")).json()["id"]
        await http.patch(f"/api/workbench/projects/{archived}", json={"archived": True})
        got = await http.post(
            f"/api/workbench/projects/{archived}/delegations", json=ask()
        )
        assert (got.status_code, got.json()["code"]) == (409, "project_archived")
    assert await count(db, "workbench_sessions") == 0
    assert await count(db, "workbench_tasks") == 0


async def test_another_user_cannot_use_or_see_it(make_client, db):  # noqa: F811
    """AT-INV-15"""
    async with make_client(A) as http:
        _, pid = await ready(http)
        made = (
            await http.post(f"/api/workbench/projects/{pid}/delegations", json=ask())
        ).json()
    async with make_client(B) as http:
        await machine(http)
        got = await http.post(f"/api/workbench/projects/{pid}/delegations", json=ask())
        assert got.status_code == 404
        for path in ("steps", "steps/1"):
            seen = await http.get(f"/api/workbench/tasks/{made['task_id']}/{path}")
            assert seen.status_code == 404
    assert await count(db, "workbench_tasks") == 1


async def test_the_home_of_the_experts_and_the_to_do_list(make_client, db):  # noqa: F811
    async with make_client(A) as http:
        _, pid = await ready(http)
        made = (
            await http.post(f"/api/workbench/projects/{pid}/delegations", json=ask())
        ).json()
        home = await http.get("/api/workbench/expert/overview")
        assert home.status_code == 200
        body = home.json()
        assert body["waiting"] == [] and body["returned"] == []
        (running,) = body["running"]
        assert running["task_id"] == made["task_id"]
        assert running["expert"] == "财报体检" and running["question"] == QUESTION
        assert running["position"]["of"] == 7

        todo = await http.get("/api/workbench/interactions")
        assert todo.status_code == 200 and todo.json()["interactions"] == []
        assert (
            await http.get("/api/workbench/interactions?status=nope")
        ).status_code == 422
        missing = await http.get(f"/api/workbench/interactions/{uuid.uuid4()}")
        assert missing.status_code == 404
    async with make_client(B) as http:
        body = (await http.get("/api/workbench/expert/overview")).json()
        assert body["running"] == []


async def test_the_working_paper_over_http(make_client, db):  # noqa: F811
    async with make_client(A) as http:
        _, pid = await ready(http)
        made = (
            await http.post(f"/api/workbench/projects/{pid}/delegations", json=ask())
        ).json()
        base = f"/api/workbench/tasks/{made['task_id']}"
        got = await http.get(f"{base}/dossier")
        assert got.status_code == 200
        body = got.json()
        assert body["head"] == {"kind": "running", "text": "专家还在做"}
        assert body["task"]["question"] == QUESTION
        assert body["conclusion"]["text"] == ""
        every = [b for s in body["sections"] for b in s["blocks"]]
        assert every and all(b["status"] == "not_reached" for b in every)

        saved = await http.put(f"{base}/conclusion", json={"text": "我的看法"})
        assert saved.status_code == 200
        again = (await http.get(f"{base}/dossier")).json()
        assert again["conclusion"]["text"] == "我的看法"

        export = await http.get(f"{base}/dossier/export")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/markdown")
        assert "attachment" in export.headers["content-disposition"]
        assert export.text.startswith(f"# {QUESTION}")
        assert "我的看法" in export.text
    async with make_client(B) as http:
        for path in ("dossier", "dossier/export"):
            assert (await http.get(f"{base}/{path}")).status_code == 404
