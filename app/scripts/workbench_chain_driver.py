"""整条链的驱动：工作台（账房 + runner，进程内）→ 沙箱 app-server（容器，ws + 令牌）→ 会合点 → 本地代理 → 本机文件。

不走 HTTP 与登录（路由另有 tests/test_workbench_routes.py），直接用应用层服务，验的是工作台与沙箱、代理之间的真实往来。
env：AGENT_TEST_DATABASE_URL（*_tests 库，脚本自建 schema 跑迁移）、APP_SERVER_URL、APP_SERVER_TOKEN、ROOT（本机白名单目录）、ENV_KEY（默认 user-pc）
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.application.workbench.ledger import Ledger  # noqa: E402
from app.application.workbench.runner import Publisher, Runner  # noqa: E402
from app.application.workbench.session_service import SessionService  # noqa: E402
from app.domain.workbench.errors import WheelHeldByOther  # noqa: E402
from app.domain.workbench.models import HandoverRequest  # noqa: E402
from app.infrastructure.workbench.repository import WorkbenchRepository  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[1]
OWNER = str(uuid.uuid4())
fails = 0


def verdict(name: str, ok: bool, extra: str = "") -> None:
    global fails
    print(f"  VERDICT {name:<58} {'pass' if ok else 'fail'} {extra}", flush=True)
    if not ok:
        fails += 1


def migrate(connection):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with Operations.context(MigrationContext.configure(connection)):
        for path in sorted((ROOT_DIR / "alembic/versions").glob("20*.py")):
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            module.upgrade()


async def wait_event(factory, sid: str, event_type: str, timeout: float = 240) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        async with factory() as s:
            for e in await WorkbenchRepository(s).list_events(session_id=sid):
                if e["type"] == event_type:
                    return e
        await asyncio.sleep(0.5)
    return None


async def pump(runner: Runner, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        if await runner.run_once() == 0:
            await asyncio.sleep(0.3)


async def main() -> int:
    url = os.environ["AGENT_TEST_DATABASE_URL"]
    app_url = os.environ.get("APP_SERVER_URL", "ws://127.0.0.1:47800")
    token = os.environ["APP_SERVER_TOKEN"]
    root = os.environ["ROOT"]
    env_key = os.environ.get("ENV_KEY", "user-pc")
    schema = "wb_chain_" + uuid.uuid4().hex[:8]
    sync = create_engine(url.replace("postgresql://", "postgresql+psycopg://"))
    with sync.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
        c.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        migrate(c)
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"), connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    runner = Runner(factory, publisher=Publisher(None, "it"), runner_id="chain-driver", environment_key=env_key, poll_seconds=0.2)
    ts = int(time.time())
    try:
        print("--- 1. 登记环境、沙箱，建会话")
        async with factory() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                env = await repo.register_environment(owner_actor_id=OWNER, name="this-box", agent_version="0.1.0", codex_version="0.155.1", roots=[root], ceiling={"sandbox": "workspace-write", "network": False})
                sb = await repo.register_sandbox(owner_actor_id=OWNER, app_server_url=app_url, token_ref=f"inline:{token}", codex_version="0.155.1")
            sid = (await SessionService(repo).create(owner_actor_id=OWNER, environment_id=env, sandbox_id=sb, project_root=root, thread_settings={"approvalPolicy": "on-request", "sandbox": "workspace-write"}))["session_id"]
            async with repo.transaction():
                await repo.enqueue_command(session_id=sid, sandbox_id=sb, kind="session.start_thread", payload={})
        await pump(runner, 2)
        async with factory() as s:
            sess = await WorkbenchRepository(s).get_session(sid)
        verdict("thread/start 经 ws+令牌成功，thread_id 落库", bool(sess["thread_id"]), str(sess["thread_id"]))

        print("--- 2. 用户自驾：在白名单目录写文件（命令在本机执行）")
        fname = f"PROBE_WB_{ts}.txt"
        async with factory() as s:
            repo = WorkbenchRepository(s)
            await SessionService(repo).record_user_turn_requested(sid, owner_actor_id=OWNER, text="t1", request_id="req-1")
            async with repo.transaction():
                await repo.enqueue_command(session_id=sid, sandbox_id=sb, kind="turn.start", payload={"text": f"Run exactly `echo via-workbench > {fname} && cat {fname}` and report verbatim.", "request_id": "req-1", "by": "user"})
        await pump(runner, 1)
        done = await wait_event(factory, sid, "turn/completed")
        verdict("turn/completed 事件到达", done is not None)
        target = Path(root) / fname
        verdict("文件出现在本机白名单目录", target.exists() and target.read_text().strip() == "via-workbench")
        async with factory() as s:
            types = [e["type"] for e in await WorkbenchRepository(s).list_events(session_id=sid)]
        verdict("事件里有 item/completed 且没有 delta", "item/completed" in types and not any(t.endswith("/delta") for t in types))

        print("--- 3. 用户自驾：写白名单外 → Codex 请求审批 → 工作台开 Interaction → 我们批准 → 本地上限仍然拒绝")
        outside = f"/home/zym/probe-wb-outside-{ts}.txt"
        async with factory() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                await repo.enqueue_command(session_id=sid, sandbox_id=sb, kind="turn.start", payload={"text": f"Run exactly `touch {outside} && echo touched`. If it needs approval, request it. Do not retry; report the final result verbatim.", "request_id": "req-2", "by": "user"})
        await pump(runner, 1)
        opened = await wait_event(factory, sid, "interaction/opened", timeout=180)
        verdict("审批请求变成 tool_approval Interaction", opened is not None and opened["payload"].get("kind") == "tool_approval")
        if opened is None:
            async with factory() as s:
                evs = await WorkbenchRepository(s).list_events(session_id=sid)
            print("  [debug] 第三段的事件：")
            for e in evs:
                if e["cursor"] > 8:
                    p = e["payload"]
                    brief = p.get("item", {}).get("text") if isinstance(p.get("item"), dict) else None
                    print("   ", e["cursor"], e["type"], (brief or str(p))[:300].replace("\n", " | "))
        if opened:
            async with factory() as s:
                repo = WorkbenchRepository(s)
                it = (await repo.list_interactions(session_id=sid, status="pending"))[0]
                await Ledger(repo).respond_interaction(str(it["id"]), token=opened["payload"]["token"], response={"decision": "accept"}, owner_actor_id=OWNER)
                async with repo.transaction():
                    await repo.enqueue_command(session_id=sid, sandbox_id=sb, kind="approval.respond", payload={"request_id": it["prompt"]["subject"]["request_id"], "decision": "accept"})
            await pump(runner, 1)
            # 第二个 turn/completed
            deadline = time.time() + 180
            second = None
            while time.time() < deadline and second is None:
                async with factory() as s:
                    evs = [e for e in await WorkbenchRepository(s).list_events(session_id=sid) if e["type"] == "turn/completed"]
                if len(evs) >= 2:
                    second = evs[1]
                await asyncio.sleep(0.5)
            verdict("批准后的 turn 结束", second is not None)
            verdict("白名单外文件没有出现（本地上限兜底）", not Path(outside).exists())
            async with factory() as s:
                r = await s.execute(text("select decision, source from workbench_approval_log order by created_at"))
                rows = [tuple(x) for x in r.all()]
            verdict("审批留痕 decision=accept source=user", rows == [("accept", "user")], str(rows))

        print("--- 4. 交出方向盘：用户 turn 被拒；取消后交回")
        async with factory() as s:
            repo = WorkbenchRepository(s)
            led = Ledger(repo)
            t = (await led.handover(HandoverRequest(idempotency_key=f"chain-{ts}", session_id=sid, profile_id="DATA_QUERY", original_input={"text": "核对"}, budget_limit=Decimal("5")), owner_actor_id=OWNER))["task_id"]
            refused = False
            try:
                await SessionService(repo).assert_user_may_drive(sid, owner_actor_id=OWNER)
            except WheelHeldByOther:
                refused = True
            verdict("advisor 持方向盘时用户 turn 被拒", refused)
            r = await led.request_cancel(t, owner_actor_id=OWNER)
            verdict("取消后 Task CANCELLED、方向盘交回", r["state"] == "CANCELLED" and (await repo.get_session(sid))["wheel"] == "user")
        try:
            target.unlink()
        except FileNotFoundError:
            pass
    finally:
        for link in runner.links.values():
            if link.client:
                await link.client.close()
        await engine.dispose()
        with sync.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        sync.dispose()
    print("结论：" + ("pass" if fails == 0 else f"fail ({fails})"))
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
