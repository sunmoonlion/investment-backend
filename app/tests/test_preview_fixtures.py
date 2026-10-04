"""给网页端的预览造样例（investment-web-frontend `preview/fixtures/`）。

各种状态由真的 runner、真的账、真的接口造出来；对面是重放磁带的假 app-server。
平时它就是一个测试：造得出来、每个接口都答得上。要把样例写进网页端的仓库：

    PREVIEW_FIXTURES_OUT=<网页端>/app/preview/fixtures uv run pytest tests/test_preview_fixtures.py

样例里的公司是真名字，数字是编的（tests/preview_samples.py）。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

from preview_recorder import Recorder
from preview_replay import HANG, Plan, ReplayAppServer
from preview_samples import (
    AIRPORT,
    BYD,
    HENGRUI,
    MERCHANTS,
    NINGDE,
    PIENTZEHUANG,
    WULIANGYE_QUESTION,
    Company,
    data_query_steps,
    data_query_tools,
    fin_review_tools,
    missing_dataset_call,
)
from sqlalchemy import text
from test_workbench_ledger_db import db as db  # noqa: F401
from test_workbench_routes import make_client as make_client  # noqa: F401

from app.bootstrap.workbench import build_runner
from app.infrastructure.workbench.publisher import RedisPublisher

WORKSPACE = "/home/demo/research"
TAPES = json.loads(
    (Path(__file__).parent / "preview_tapes" / "conversations.json").read_text()
)["conversations"]
USER = str(uuid.uuid5(uuid.NAMESPACE_URL, "preview:full"))
NEWCOMER = str(uuid.uuid5(uuid.NAMESPACE_URL, "preview:empty"))
AWAY = str(uuid.uuid5(uuid.NAMESPACE_URL, "preview:offline"))

MOUTAI = Company(
    "贵州茅台",
    "600519",
    "sh",
    revenue=150_560_000_000,
    growth=(0.157, 0.094),
    gross=(0.919, 0.920, 0.918),
    net=(0.525, 0.522, 0.519),
    leverage=0.18,
)


def tape(name: str) -> list[dict[str, Any]]:
    return json.loads(json.dumps(TAPES[name]["turns"]))


def asked_about_a_company_without_data() -> dict[str, Any]:
    """聊天里问到一家没有数据的公司。这一轮不是录的，是照磁带的样子拼的。"""

    def note(method: str, **params: Any) -> dict[str, Any]:
        return {
            "kind": "notification",
            "method": method,
            "params": {"threadId": "T", "turnId": "U", **params},
        }

    return {
        "prompt": "片仔癀 2025 年的毛利率是多少？",
        "messages": [
            note("turn/started", turn={"id": "U", "status": "inProgress"}),
            note(
                "item/completed",
                item={"id": "call-1", **missing_dataset_call("600436")},
            ),
            note(
                "item/completed",
                item={
                    "type": "agentMessage",
                    "id": "msg-1",
                    "text": "片仔癀（600436）的数据还没有入库，我查不到它的财务数据。"
                    "可以先申请入库，入库之后再来问。",
                },
            ),
            note(
                "thread/tokenUsage/updated", tokenUsage={"total": {"totalTokens": 3200}}
            ),
            {
                "kind": "notification",
                "method": "turn/completed",
                "params": {"threadId": "T", "turn": {"id": "U", "status": "completed"}},
            },
        ],
    }


class World:
    def __init__(self, http, runner, fake, recorder: Recorder) -> None:
        self.http, self.runner, self.fake, self.rec = http, runner, fake, recorder
        self.environment = ""
        self.projects: dict[str, str] = {}
        self.sessions: dict[str, str] = {}
        self.tasks: dict[str, str] = {}

    async def turn_until(self, check, seconds: float = 20.0) -> bool:
        for _ in range(int(seconds / 0.05)):
            await self.runner.run_once()
            if await check():
                return True
            await asyncio.sleep(0.05)
        return False

    async def events(self, session: str) -> list[dict[str, Any]]:
        got = await self.http.get(
            f"/api/workbench/sessions/{session}/events", params={"limit": 1000}
        )
        return got.json()["events"]

    async def task(self, name: str) -> dict[str, Any]:
        got = await self.http.get(f"/api/workbench/tasks/{self.tasks[name]}")
        return got.json()["task"]

    async def machine(self, name: str = "办公室的电脑") -> None:
        made = await self.rec.call(
            self.http,
            "POST",
            "/api/workbench/environments",
            expect=201,
            json_body={
                "name": name,
                "roots": [WORKSPACE, "/home/demo/notes"],
                "agent_version": "0.1.0",
                "codex_version": "0.155.1",
            },
        )
        self.environment = made["environment_id"]
        await self.http.post(
            "/api/workbench/sandboxes",
            json={
                "app_server_url": f"ws://127.0.0.1:{self.fake.port}",
                "token_ref": "inline:preview",
                "codex_version": "0.155.1",
            },
        )
        await self.rec.call(
            self.http,
            "PUT",
            "/api/workbench/prefs",
            json_body={"model": "kimi-k3", "approval_policy": "on-request"},
        )

    async def project(self, name: str, *, title: str | None = None) -> str:
        made = await self.rec.call(
            self.http,
            "POST",
            "/api/workbench/projects",
            expect=201,
            json_body={
                "environment_id": self.environment,
                "workspace_root": WORKSPACE,
                "path": name,
                **({"title": title} if title else {}),
            },
        )
        self.projects[name] = made["id"]
        return made["id"]

    async def conversation(
        self, name: str, plan: Plan, *, kind: str, project: str | None = None
    ) -> str:
        self.fake.next_thread(plan)
        made = await self.rec.call(
            self.http,
            "POST",
            "/api/workbench/sessions",
            expect=201,
            json_body={
                "kind": kind,
                **({"project_id": self.projects[project]} if project else {}),
            },
        )
        session = made["session_id"]
        self.sessions[name] = session

        async def has_thread() -> bool:
            got = await self.http.get(f"/api/workbench/sessions/{session}")
            return bool(got.json()["session"]["thread_id"])

        assert await self.turn_until(has_thread), name
        return session

    async def say(self, name: str, prompt: str, *, waits_for_approval: bool = False):
        session = self.sessions[name]
        before = await self.events(session)
        done = sum(1 for e in before if e["type"] == "turn/completed")
        await self.rec.call(
            self.http,
            "POST",
            f"/api/workbench/sessions/{session}/turns",
            expect=202,
            json_body={"text": prompt},
        )

        async def over() -> bool:
            now = await self.events(session)
            if waits_for_approval:
                return any(e["type"] == "interaction/opened" for e in now)
            return sum(1 for e in now if e["type"] == "turn/completed") > done

        assert await self.turn_until(over), (name, prompt)

    async def talk(
        self, name: str, taped: str, *, kind: str, project: str | None = None
    ):
        turns = tape(taped)
        await self.conversation(name, Plan(tape=turns), kind=kind, project=project)
        for n, turn in enumerate(turns):
            asks = any(m["kind"] == "request" for m in turn["messages"])
            await self.say(name, turn["prompt"], waits_for_approval=asks)
            assert not asks or n == len(turns) - 1

    async def delegate(
        self,
        name: str,
        *,
        project: str,
        expert: str,
        question: str,
        replies: list[Any],
        tools: dict[str, list[dict[str, Any]]],
        budget: str = "2",
        tokens: int = 5000,
        until: str,
    ) -> str:
        self.fake.next_thread(Plan(expert=replies, tools=tools, tokens=tokens))
        made = await self.rec.call(
            self.http,
            "POST",
            f"/api/workbench/projects/{self.projects[project]}/delegations",
            expect=201,
            json_body={
                "idempotency_key": f"preview-{name}-0001",
                "expert": expert,
                "question": question,
                "budget_limit": budget,
            },
        )
        self.tasks[name] = made["task_id"]
        self.sessions[name] = made["session_id"]
        await self.reach(name, until)
        return made["task_id"]

    async def reach(self, name: str, state: str) -> None:
        async def there() -> bool:
            task = await self.task(name)
            if state == "RUNNING":
                return (
                    task["state"] == "RUNNING" and task["active_attempt_id"] is not None
                )
            return task["state"] == state

        assert await self.turn_until(there, 40), (name, state, await self.task(name))

    async def answer(self, name: str, decision: str) -> None:
        task = await self.task(name)
        waiting = task["active_interaction_id"]
        token = next(
            e["payload"]["token"]
            for e in await self.events(self.sessions[name])
            if e["type"] == "interaction/opened"
            and e["payload"]["interaction_id"] == waiting
        )
        await self.rec.call(
            self.http,
            "POST",
            f"/api/workbench/interactions/{waiting}/respond",
            expect=200,
            json_body={"token": token, "decision": decision},
        )


def fin_review(company: Company, **changes: Any) -> list[Any]:
    steps = {
        "scope": company.scope(),
        "profile": company.profile(),
        "reconcile": company.reconcile(),
        "extract": company.facts(),
        "metrics": company.metrics(),
        "crosscheck": company.crosscheck(),
        "note": company.note(),
    }
    out: list[Any] = []
    for step, reply in steps.items():
        extra = changes.get(step)
        if extra is None:
            out.append(reply)
        elif isinstance(extra, list):
            out.extend(extra)
        else:
            out.append(extra)
        if changes.get("stop_after") == step:
            break
    return out


async def record_everything(world: World) -> None:
    rec, http = world.rec, world.http
    for path in (
        "workspaces",
        "environments",
        "sandboxes",
        "projects",
        "sessions",
        "tasks",
        "interactions",
        "expert/overview",
        "packs",
        "prefs",
        "credentials",
    ):
        await rec.get(http, f"/api/workbench/{path}")
    await rec.get(http, "/api/workbench/projects", include_archived="true")
    await rec.get(http, "/api/workbench/sessions", without_project="true")
    await rec.get(http, "/api/workbench/interactions", status="all")
    for pack in (await http.get("/api/workbench/packs")).json()["packs"]:
        await rec.get(http, f"/api/workbench/packs/{pack['id']}")
    for project in world.projects.values():
        await rec.get(http, f"/api/workbench/projects/{project}")
        await rec.get(http, "/api/workbench/sessions", project_id=project)
    sessions = (await http.get("/api/workbench/sessions")).json()["sessions"]
    for session in sessions:
        sid = session["id"]
        await rec.get(http, f"/api/workbench/sessions/{sid}")
        await rec.get(http, "/api/workbench/tasks", session_id=sid)
        events = await rec.get(
            http, f"/api/workbench/sessions/{sid}/events", limit=1000
        )
        rec.stream(f"/api/workbench/sessions/{sid}/stream", events["events"])
    for task in (await http.get("/api/workbench/tasks")).json()["tasks"]:
        tid = task["id"]
        base = f"/api/workbench/tasks/{tid}"
        await rec.get(http, base)
        await rec.get(http, f"{base}/artifacts")
        await rec.get(http, f"{base}/dossier")
        await rec.get(http, f"{base}/dossier/export")
        shown = await rec.get(http, f"{base}/steps")
        for step in shown["steps"]:
            await rec.get(http, f"{base}/steps/{step['index']}")
    every = await http.get("/api/workbench/interactions", params={"status": "all"})
    for waiting in every.json()["interactions"]:
        await rec.get(http, f"/api/workbench/interactions/{waiting['interaction_id']}")


async def build_full(world: World) -> None:
    rec, http = world.rec, world.http
    await world.machine()
    for name in (
        "恒瑞医药",
        "贵州茅台",
        "五粮液",
        "宁德时代",
        "片仔癀",
        "上海机场",
        "比亚迪",
    ):
        await world.project(name)
    await world.project("招商银行")
    await world.project("中国平安")
    old = await world.project("2024/旧的研究", title="旧的研究")
    await rec.call(
        http, "PATCH", f"/api/workbench/projects/{old}", json_body={"archived": True}
    )

    # 已完成：算指标重做过一次
    await world.delegate(
        "done-redone",
        project="恒瑞医药",
        expert="FIN_REVIEW",
        question=HENGRUI.question(),
        replies=fin_review(
            HENGRUI, metrics=[HENGRUI.metrics(empty=True), HENGRUI.metrics()]
        ),
        tools=fin_review_tools(HENGRUI),
        until="SUCCEEDED",
    )
    # 已完成：用户写了自己的结论
    await world.delegate(
        "done-concluded",
        project="贵州茅台",
        expert="FIN_REVIEW",
        question=MOUTAI.question(),
        replies=fin_review(MOUTAI),
        tools=fin_review_tools(MOUTAI),
        until="SUCCEEDED",
    )
    await rec.call(
        http,
        "PUT",
        f"/api/workbench/tasks/{world.tasks['done-concluded']}/conclusion",
        expect=200,
        json_body={
            "text": "毛利率三年都在九成以上，变化不大。\n口径在 2024 年变过一次，和前后两年不能直接比。\n（样例：这是用户自己写的草稿）"
        },
    )
    # 进行中：问数做到第 3 步
    await world.delegate(
        "running",
        project="五粮液",
        expert="DATA_QUERY",
        question=WULIANGYE_QUESTION,
        replies=[*data_query_steps()[:2], HANG],
        tools=data_query_tools(),
        until="RUNNING",
    )

    async def third_step() -> bool:
        return (await world.task("running"))["current_step"] == 2

    assert await world.turn_until(third_step, 30)
    # 等我决定：勾稽不平
    await world.delegate(
        "waiting",
        project="宁德时代",
        expert="FIN_REVIEW",
        question=NINGDE.question(),
        replies=fin_review(
            NINGDE, reconcile=NINGDE.reconcile(unbalanced=True), stop_after="reconcile"
        ),
        tools=fin_review_tools(NINGDE),
        until="WAITING",
    )
    # 没有数据
    await world.delegate(
        "no-data",
        project="片仔癀",
        expert="FIN_REVIEW",
        question=PIENTZEHUANG.question(),
        replies=fin_review(
            PIENTZEHUANG, profile=PIENTZEHUANG.not_ingested(), stop_after="profile"
        ),
        tools={"profile": fin_review_tools(PIENTZEHUANG)["profile"][:1]},
        until="WAITING",
    )
    # 预算用完
    await world.delegate(
        "over-budget",
        project="上海机场",
        expert="FIN_REVIEW",
        question=AIRPORT.question(),
        replies=fin_review(AIRPORT),
        tools=fin_review_tools(AIRPORT),
        budget="1",
        tokens=30000,
        until="WAITING",
    )
    # 失败：和官方对不上，用户选了停止
    await world.delegate(
        "failed",
        project="比亚迪",
        expert="FIN_REVIEW",
        question=BYD.question(),
        replies=fin_review(
            BYD, crosscheck=BYD.crosscheck(mismatch=True), stop_after="crosscheck"
        ),
        tools=fin_review_tools(BYD),
        until="WAITING",
    )
    await world.answer("failed", "stop")
    await world.reach("failed", "FAILED")
    # 已取消：停下来问的时候用户取消了
    await world.delegate(
        "cancelled",
        project="招商银行",
        expert="FIN_REVIEW",
        question=MERCHANTS.question(),
        replies=fin_review(
            MERCHANTS,
            reconcile=MERCHANTS.reconcile(unbalanced=True),
            stop_after="reconcile",
        ),
        tools=fin_review_tools(MERCHANTS),
        until="WAITING",
    )
    await rec.call(
        http,
        "POST",
        f"/api/workbench/tasks/{world.tasks['cancelled']}/cancel",
        expect=202,
    )
    await world.reach("cancelled", "CANCELLED")

    # 三段对话：不属于项目的聊天、项目里的聊天、工作（最后停在等用户批准）
    free = tape("chat")
    free.append(asked_about_a_company_without_data())
    await world.conversation("chat", Plan(tape=free), kind="chat")
    for turn in free:
        await world.say("chat", turn["prompt"])
    await world.talk("project-chat", "project_chat", kind="chat", project="恒瑞医药")
    await world.talk("work", "work", kind="work", project="恒瑞医药")

    # 打回：在对话里请了一位不存在的专家
    await world.conversation("refused", Plan(), kind="work", project="中国平安")
    asked = await rec.call(
        http,
        "POST",
        f"/api/workbench/sessions/{world.sessions['refused']}/handover",
        expect=201,
        json_body={
            "idempotency_key": "preview-refused-0001",
            "profile_id": "BOND_REVIEW",
            "original_input": {"text": "中国平安 2025 年的偿付能力怎么样？"},
            "budget_limit": "2",
        },
    )
    world.tasks["refused"] = asked["task_id"]
    await world.reach("refused", "REJECTED")

    # 这个项目里专家还在做：再请一次交不出去
    await rec.call(
        http,
        "POST",
        f"/api/workbench/projects/{world.projects['五粮液']}/delegations",
        expect=409,
        json_body={
            "idempotency_key": "preview-busy-0001",
            "expert": "DATA_QUERY",
            "question": "五粮液近五年的净利率分别是多少？",
            "budget_limit": "2",
        },
    )

    await record_everything(world)
    p, s, t = world.projects, world.sessions, world.tasks
    rec.page("首页", "/zh-CN/workbench")
    rec.page("专家首页（等我决定、进行中、最近交回）", "/zh-CN/workbench/expert")
    rec.page("项目列表", "/zh-CN/workbench/projects")
    rec.page("项目页：恒瑞医药", f"/zh-CN/workbench/projects/{p['恒瑞医药']}")
    rec.page(
        "聊天（不属于项目；问到一家没有数据的公司）",
        f"/zh-CN/workbench/chat/{s['chat']}",
    )
    rec.page(
        "项目里的聊天（只读）",
        f"/zh-CN/workbench/projects/{p['恒瑞医药']}/c/{s['project-chat']}",
    )
    rec.page(
        "工作（有一条命令等我批准）",
        f"/zh-CN/workbench/projects/{p['恒瑞医药']}/c/{s['work']}",
    )
    rec.page("请专家", f"/zh-CN/workbench/projects/{p['恒瑞医药']}/expert/new")
    for title, project, name in (
        ("专家处理中：问数做到第 3 步", "五粮液", "running"),
        ("专家停下来了：勾稽不平", "宁德时代", "waiting"),
        ("专家停下来了：没有数据", "片仔癀", "no-data"),
        ("专家停下来了：预算用完", "上海机场", "over-budget"),
    ):
        rec.page(title, f"/zh-CN/workbench/projects/{p[project]}/c/{s[name]}")
    for title, project, name in (
        ("底稿：已完成，算指标重做过一次", "恒瑞医药", "done-redone"),
        ("底稿：已完成，用户写了结论", "贵州茅台", "done-concluded"),
        ("底稿：失败，做到第 6 步", "比亚迪", "failed"),
        ("底稿：已取消", "招商银行", "cancelled"),
        ("底稿：打回", "中国平安", "refused"),
        ("底稿：还在做", "五粮液", "running"),
    ):
        rec.page(title, f"/zh-CN/workbench/projects/{p[project]}/tasks/{t[name]}")
    every = await http.get("/api/workbench/interactions")
    for waiting in every.json()["interactions"]:
        where = waiting["where"]
        step = where["step"]
        about = where["about"]
        if step:
            about += f"（第 {step['index']} 步 {step['title']}）"
        if waiting["missing_data"]:
            about = "没有数据"
        rec.page(
            f"审查面：{about}", f"/zh-CN/workbench/review/{waiting['interaction_id']}"
        )
    rec.page("我的机器", "/zh-CN/workbench/machines")
    rec.page("设置", "/zh-CN/workbench/settings")


async def build_empty(world: World) -> None:
    """刚注册的人：没有机器、没有沙箱、没有项目。"""
    rec, http = world.rec, world.http
    for path in (
        "workspaces",
        "environments",
        "sandboxes",
        "projects",
        "sessions",
        "tasks",
        "interactions",
        "expert/overview",
        "packs",
        "prefs",
        "credentials",
    ):
        await rec.get(http, f"/api/workbench/{path}")
    await rec.call(
        http,
        "POST",
        "/api/workbench/sessions",
        expect=409,
        json_body={"kind": "chat"},
    )
    rec.page("首页（什么都还没有）", "/zh-CN/workbench")
    rec.page("专家首页", "/zh-CN/workbench/expert")
    rec.page("项目列表（空的）", "/zh-CN/workbench/projects")
    rec.page("我的机器（还没有装）", "/zh-CN/workbench/machines")
    rec.page("设置", "/zh-CN/workbench/settings")


async def build_offline(world: World, db) -> None:  # noqa: F811
    """机器离线：聊天可用；新建工作、请专家用不了；已有的对话与底稿可以看。"""
    rec, http = world.rec, world.http
    await world.machine("家里的电脑")
    await world.project("恒瑞医药")
    await world.delegate(
        "done",
        project="恒瑞医药",
        expert="FIN_REVIEW",
        question=HENGRUI.question(),
        replies=fin_review(HENGRUI),
        tools=fin_review_tools(HENGRUI),
        until="SUCCEEDED",
    )
    await world.talk("project-chat", "project_chat", kind="chat", project="恒瑞医药")
    async with db() as s:
        await s.execute(text("update workbench_environments set status = 'offline'"))
        await s.commit()
    await rec.call(
        http,
        "POST",
        f"/api/workbench/projects/{world.projects['恒瑞医药']}/delegations",
        expect=409,
        json_body={
            "idempotency_key": "preview-offline-0001",
            "expert": "FIN_REVIEW",
            "question": HENGRUI.question(),
            "budget_limit": "2",
        },
    )
    await record_everything(world)
    p, s, t = world.projects, world.sessions, world.tasks
    rec.page("首页", "/zh-CN/workbench")
    rec.page("项目页", f"/zh-CN/workbench/projects/{p['恒瑞医药']}")
    rec.page(
        "请专家（机器离线，交不出去）",
        f"/zh-CN/workbench/projects/{p['恒瑞医药']}/expert/new",
    )
    rec.page(
        "项目里的聊天",
        f"/zh-CN/workbench/projects/{p['恒瑞医药']}/c/{s['project-chat']}",
    )
    rec.page("底稿", f"/zh-CN/workbench/projects/{p['恒瑞医药']}/tasks/{t['done']}")
    rec.page("我的机器（离线）", "/zh-CN/workbench/machines")


SCENARIOS = {
    "full": (
        USER,
        "什么都有",
        "一个用了一阵子的人：十个项目，各种状态的委托都有——进行中、等我决定、没有数据、"
        "预算用完、已完成、失败、已取消、打回；三段对话，其中一段有命令等着批准。",
    ),
    "empty": (
        NEWCOMER,
        "刚注册",
        "没有机器、没有沙箱、没有项目。看各个页面空着的样子。",
    ),
    "offline": (AWAY, "机器离线", "有一个项目、一份底稿、一段聊天，但机器不在线。"),
}


async def run(
    scenario: str,
    make_client,  # noqa: F811
    db,  # noqa: F811
    out: Path,
    *,
    someone_else: str | None = None,
) -> Path:
    actor, title, description = SCENARIOS[scenario]
    actor = someone_else or actor
    fake = ReplayAppServer()
    await fake.start()
    runner = build_runner(
        db,
        publisher=RedisPublisher(None, "preview"),
        runner_id=f"preview-{scenario}",
        poll_seconds=0.05,
    )
    recorder = Recorder(scenario, title=title, description=description)
    try:
        async with make_client(actor) as http:
            world = World(http, runner, fake, recorder)
            if scenario == "full":
                await build_full(world)
            elif scenario == "empty":
                await build_empty(world)
            else:
                await build_offline(world, db)
        return recorder.write(out)
    finally:
        for link in runner.links.values():
            if link.client:
                await link.client.close()
        for driving in runner.driving.values():
            driving.cancel()
        await fake.close()


def where_to(tmp_path: Path) -> Path:
    wanted = os.environ.get("PREVIEW_FIXTURES_OUT")
    return Path(wanted) if wanted else tmp_path


def check(directory: Path) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["pages"]
    for response in manifest["responses"]:
        body = (directory / response["file"]).read_text()
        if response["method"] == "GET":
            assert response["status"] == 200, response
        if response["file"].endswith(".json") and body.strip():
            json.loads(body)
        # 方法的原文、密钥不在样例里
        assert "Advisor step" not in body and "Reply with ONE JSON" not in body
        assert "sk-" not in body
    return manifest


async def test_the_full_world(make_client, db, tmp_path):  # noqa: F811
    manifest = check(await run("full", make_client, db, where_to(tmp_path)))
    directory = where_to(tmp_path) / "full"

    def answer(path: str, query: str = "") -> Any:
        found = next(
            r
            for r in manifest["responses"]
            if r["method"] == "GET" and r["path"] == path and r["query"] == query
        )
        return json.loads((directory / found["file"]).read_text())

    home = answer("/api/workbench/expert/overview")
    assert len(home["waiting"]) == 3  # 勾稽不平、没有数据、预算用完
    assert [r["state"] for r in home["running"]] == ["RUNNING"]
    assert all(r["reason_text"] for r in home["returned"] if r["state"] != "SUCCEEDED")
    assert {r["state_word"] for r in home["returned"]} == {
        "已完成",
        "失败",
        "已取消",
        "打回",
    }
    # 对话里等着批准的命令也在待办里，但不在专家首页
    todo = answer("/api/workbench/interactions")["interactions"]
    assert sorted(w["kind"] for w in todo) == [
        "input",
        "input",
        "resource",
        "tool_approval",
    ]
    assert len(answer("/api/workbench/projects")["projects"]) == 9
    assert (
        len(answer("/api/workbench/projects", "include_archived=true")["projects"])
        == 10
    )
    assert len(manifest["streams"]) == len(
        answer("/api/workbench/sessions")["sessions"]
    )
    # 编号是固定的：换一个人重新录一遍，地址不变
    again = await run(
        "full", make_client, db, tmp_path / "again", someone_else=str(uuid.uuid4())
    )
    assert (
        json.loads((again / "manifest.json").read_text())["pages"] == manifest["pages"]
    )


async def test_a_newcomer(make_client, db, tmp_path):  # noqa: F811
    manifest = check(await run("empty", make_client, db, where_to(tmp_path)))
    refused = [r for r in manifest["responses"] if r["method"] == "POST"]
    assert [(r["path"], r["status"]) for r in refused] == [
        ("/api/workbench/sessions", 409)
    ]


async def test_a_machine_that_is_offline(make_client, db, tmp_path):  # noqa: F811
    manifest = check(await run("offline", make_client, db, where_to(tmp_path)))
    directory = where_to(tmp_path) / "offline"
    spaces = next(
        r for r in manifest["responses"] if r["path"] == "/api/workbench/workspaces"
    )
    body = json.loads((directory / spaces["file"]).read_text())
    assert [w["online"] for w in body["workspaces"]] == [False, False]
    refused = [
        r
        for r in manifest["responses"]
        if r["path"].endswith("/delegations") and r["status"] == 409
    ]
    assert len(refused) == 1
