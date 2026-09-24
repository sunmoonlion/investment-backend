"""在线跑臂（专家包发布门的数据采集）：每案每臂一个新会话（新 thread），基线 = 用户一个 turn 直接用 MCP；pack = 交出方向盘由顾问按 DATA_QUERY 驾驶。

不在在线路径上（0007）。费用按 Pricing 粗表或 Task 预算账。评测账户与 key 应与用户分开（F-EVAL-06）：本脚本用 APP_SERVER_* 指到的沙箱。
env：AGENT_TEST_DATABASE_URL、APP_SERVER_URL、APP_SERVER_TOKEN、ROOT、EVAL_DATASET（sqlite 路径）、ENV_KEY（默认 user-pc）
用法：uv run python -m eval.run_eval --limit 2 --arms baseline,pack --out eval/reports
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.application.workbench.advisor import Pricing  # noqa: E402
from app.application.workbench.ledger import Ledger  # noqa: E402
from app.application.workbench.runner import Publisher, Runner  # noqa: E402
from app.application.workbench.session_service import SessionService  # noqa: E402
from app.domain.workbench.models import HandoverRequest  # noqa: E402
from app.infrastructure.workbench.repository import WorkbenchRepository  # noqa: E402
from eval.cases import Case, load_cases  # noqa: E402
from eval.judge import ArmOutput, CaseVerdict, judge  # noqa: E402
from eval.report import release_gate, render_markdown, summarize  # noqa: E402
from eval.truth import TruthStore  # noqa: E402

ROOT_DIR = Path(__file__).resolve().parents[1]
OWNER = str(uuid.uuid4())
BASELINE_PROMPT = (
    "You have the MCP server `sunmoon_knowledge` with tools describe_schema, metric_definitions and run_sql "
    "over a retail operations dataset. Answer the question below with real numbers from the dataset. "
    "Use the tools; do not invent data. Reply with ONE JSON object only (no fences): "
    '{"sql": ["every SQL statement you ran"], "data_version": "the data_version reported by the tools", '
    '"answer": "your answer in Chinese"}.\n\nQuestion: '
)


def migrate(connection) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with Operations.context(MigrationContext.configure(connection)):
        for path in sorted((ROOT_DIR / "alembic/versions").glob("20*.py")):
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)
            module.upgrade()


def extract_json(text_: str | None) -> dict[str, Any]:
    if not text_:
        return {}
    body = text_.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    if m:
        body = m.group(1)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < 0:
        return {}
    try:
        v = json.loads(body[start : end + 1])
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def sqls_from_events(
    events: list[dict[str, Any]],
    *,
    turn_id: str | None = None,
    task_id: str | None = None,
) -> tuple[list[str], list[str]]:
    """从事件里取真正跑过的 run_sql 参数与工具回报的 data_version（不信模型的自述）。"""
    sqls: list[str] = []
    versions: list[str] = []
    for e in events:
        if e["type"] != "item/completed":
            continue
        p = e["payload"]
        if turn_id and p.get("turnId") != turn_id:
            continue
        if task_id and str(e.get("task_id") or "") != task_id:
            continue
        item = p.get("item") or {}
        if item.get("type") != "mcpToolCall":
            continue
        if item.get("tool") == "run_sql":
            sql = (item.get("arguments") or {}).get("sql")
            if sql:
                sqls.append(str(sql))
        res = item.get("result") or {}
        sc = res.get("structuredContent") if isinstance(res, dict) else None
        if isinstance(sc, dict) and isinstance(sc.get("citation"), dict):
            v = sc["citation"].get("data_version")
            if v:
                versions.append(str(v))
    return sqls, versions


def event_digest(events: list[dict[str, Any]]) -> list[str]:
    """报告里留一份可读的事件摘要：类型 + item 类型/工具/状态，便于事后看臂到底做了什么。"""
    out: list[str] = []
    for e in events:
        p = e["payload"]
        item = p.get("item") if isinstance(p.get("item"), dict) else None
        if item:
            brief = f"{item.get('type')}"
            if item.get("type") == "mcpToolCall":
                brief += (
                    f":{item.get('server')}/{item.get('tool')}:{item.get('status')}"
                )
            elif item.get("type") == "commandExecution":
                brief += f":{str(item.get('command'))[:60]}"
            out.append(f"{e['cursor']} {e['type']} {brief}")
        else:
            out.append(f"{e['cursor']} {e['type']}")
    return out


class Bench:
    def __init__(self, factory, runner: Runner, env_id: str, sb_id: str, root: str):
        self.factory = factory
        self.runner = runner
        self.env_id = env_id
        self.sb_id = sb_id
        self.root = root
        self.pricing = Pricing()

    async def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if await self.runner.run_once() == 0:
                await asyncio.sleep(0.3)

    async def new_session(self) -> tuple[str, str]:
        async with self.factory() as s:
            repo = WorkbenchRepository(s)
            sid = (
                await SessionService(repo).create(
                    owner_actor_id=OWNER,
                    environment_id=self.env_id,
                    sandbox_id=self.sb_id,
                    project_root=self.root,
                    thread_settings={
                        "approvalPolicy": "on-request",
                        "sandbox": "workspace-write",
                    },
                )
            )["session_id"]
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid,
                    sandbox_id=self.sb_id,
                    kind="session.start_thread",
                    payload={},
                )
        for _ in range(60):
            await self.pump(1)
            async with self.factory() as s:
                sess = await WorkbenchRepository(s).get_session(sid)
            if sess["thread_id"]:
                return sid, str(sess["thread_id"])
        raise RuntimeError("thread/start did not complete")

    async def decline_pending(self, sid: str) -> None:
        async with self.factory() as s:
            repo = WorkbenchRepository(s)
            evs = await repo.list_events(session_id=sid)
            for pend in await repo.list_interactions(session_id=sid, status="pending"):
                if pend["kind"] != "tool_approval":
                    continue
                tok = next(
                    (
                        e["payload"]["token"]
                        for e in evs
                        if e["type"] == "interaction/opened"
                        and e["payload"].get("interaction_id") == str(pend["id"])
                    ),
                    None,
                )
                if not tok:
                    continue
                await Ledger(repo).respond_interaction(
                    str(pend["id"]),
                    token=tok,
                    response={"decision": "decline"},
                    owner_actor_id=OWNER,
                )
                async with repo.transaction():
                    await repo.enqueue_command(
                        session_id=sid,
                        sandbox_id=self.sb_id,
                        kind="approval.respond",
                        payload={
                            "request_id": pend["prompt"]["subject"]["request_id"],
                            "decision": "decline",
                        },
                    )

    async def events(self, sid: str) -> list[dict[str, Any]]:
        async with self.factory() as s:
            return await WorkbenchRepository(s).list_events(session_id=sid)

    # ---------------- 臂 ----------------
    async def baseline(self, case: Case, timeout: float) -> ArmOutput:  # noqa: ASYNC109
        sid, thread_id = await self.new_session()
        async with self.factory() as s:
            repo = WorkbenchRepository(s)
            await SessionService(repo).record_user_turn_requested(
                sid,
                owner_actor_id=OWNER,
                text=case.question,
                request_id=f"eval-{case.case_id}",
            )
            link = await self.runner.link_for(self.sb_id, repo)
        turn = asyncio.create_task(
            link.run_turn(thread_id, BASELINE_PROMPT + case.question, timeout=timeout)
        )
        started = time.time()
        while not turn.done():
            await self.pump(1)
            await self.decline_pending(sid)
            if time.time() - started > timeout + 30:
                turn.cancel()
                return ArmOutput(
                    case.case_id,
                    "baseline",
                    state="TIMEOUT",
                    error="baseline turn timeout",
                )
        res = turn.result()
        evs = await self.events(sid)
        sqls, versions = sqls_from_events(evs, turn_id=res.turn_id)
        said = extract_json(res.final_text)
        for s_ in said.get("sql") or []:
            if isinstance(s_, str) and s_ not in sqls:
                sqls.append(s_)
        cited = [str(said["data_version"])] if said.get("data_version") else []
        out = ArmOutput(
            case.case_id,
            "baseline",
            sqls=sqls,
            data_versions=cited,
            cost=self.pricing.cost(res.tokens),
            tokens=int(
                (res.tokens.get("total") or res.tokens or {}).get("totalTokens") or 0
            ),
            turns=1,
            state="COMPLETED" if not res.error else "FAILED",
            error=res.error,
            extra={
                "tool_versions": versions,
                "answer": said.get("answer"),
                "session_id": sid,
                "events": event_digest(evs),
            },
        )
        return out

    async def pack(self, case: Case, budget: Decimal, timeout: float) -> ArmOutput:  # noqa: ASYNC109
        sid, _thread_id = await self.new_session()
        async with self.factory() as s:
            repo = WorkbenchRepository(s)
            led = Ledger(repo)
            task_id = (
                await led.handover(
                    HandoverRequest(
                        idempotency_key=f"eval-{case.case_id}-{uuid.uuid4().hex[:6]}",
                        session_id=sid,
                        profile_id="DATA_QUERY",
                        original_input={"text": case.question},
                        budget_limit=budget,
                    ),
                    owner_actor_id=OWNER,
                )
            )["task_id"]
            async with repo.transaction():
                await repo.enqueue_command(
                    session_id=sid,
                    sandbox_id=self.sb_id,
                    kind="task.drive",
                    payload={"task_id": task_id},
                )
        await self.runner.run_once()
        await self.runner.wait_drivers(timeout=timeout)
        async with self.factory() as s:
            repo = WorkbenchRepository(s)
            task = await repo.get_task(task_id)
            arts = await repo.list_artifacts_with_content(task_id)
            attempts = await repo.list_attempts(task_id)
        evs = await self.events(sid)
        sqls, versions = sqls_from_events(evs, task_id=task_id)
        for a in arts:
            c = a["content"] if isinstance(a["content"], dict) else {}
            if a["name"] == "sql" and c.get("sql") and c["sql"] not in sqls:
                sqls.append(str(c["sql"]))
        cited = [
            str(a["content"].get("data_version"))
            for a in arts
            if a["name"] == "rows"
            and isinstance(a["content"], dict)
            and a["content"].get("data_version")
        ]
        state = (
            "COMPLETED"
            if task["state"] == "SUCCEEDED"
            else ("WAITING" if task["state"] == "WAITING" else "FAILED")
        )
        return ArmOutput(
            case.case_id,
            "pack",
            sqls=sqls,
            data_versions=cited,
            cost=Decimal(str(task["budget"]["used"])),
            turns=len(attempts),
            state=state,
            error=None
            if state == "COMPLETED"
            else f"{task['state']} {task.get('waiting_reason') or ''} {task.get('rejection') or ''}",
            extra={
                "tool_versions": versions,
                "artifacts": [(a["name"], a["version"]) for a in arts],
                "session_id": sid,
                "task_id": task_id,
                "events": event_digest(evs),
            },
        )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2)
    ap.add_argument("--case-ids", default="")
    ap.add_argument("--arms", default="baseline,pack")
    ap.add_argument("--budget", default="8")
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--out", default=str(ROOT_DIR / "eval/reports"))
    args = ap.parse_args()

    url = os.environ["AGENT_TEST_DATABASE_URL"]
    app_url = os.environ.get("APP_SERVER_URL", "ws://127.0.0.1:47800")
    token = os.environ["APP_SERVER_TOKEN"]
    root = os.environ["ROOT"]
    dataset = Path(os.environ["EVAL_DATASET"])
    env_key = os.environ.get("ENV_KEY", "user-pc")
    store = TruthStore(dataset)
    version = store.data_version()

    cases = load_cases()
    if args.case_ids:
        wanted = set(args.case_ids.split(","))
        cases = [c for c in cases if c.case_id in wanted]
    cases = cases[: args.limit]
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]

    schema = "wb_eval_" + uuid.uuid4().hex[:8]
    sync = create_engine(url.replace("postgresql://", "postgresql+psycopg://"))
    with sync.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
        c.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        migrate(c)
    engine = create_async_engine(
        url.replace("postgresql://", "postgresql+asyncpg://"),
        connect_args={"server_settings": {"search_path": schema}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    runner = Runner(
        factory,
        publisher=Publisher(None, "eval"),
        runner_id="eval",
        environment_key=env_key,
        poll_seconds=0.2,
    )
    verdicts: list[CaseVerdict] = []
    raw: list[dict[str, Any]] = []
    ts = time.strftime("%Y%m%d-%H%M%S")
    try:
        async with factory() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                env_id = await repo.register_environment(
                    owner_actor_id=OWNER,
                    name="eval-box",
                    agent_version="0.1.0",
                    codex_version="0.155.1",
                    roots=[root],
                    ceiling={"sandbox": "workspace-write", "network": False},
                )
                sb_id = await repo.register_sandbox(
                    owner_actor_id=OWNER,
                    app_server_url=app_url,
                    token_ref=f"inline:{token}",
                    codex_version="0.155.1",
                )
        bench = Bench(factory, runner, env_id, sb_id, root)
        print(f"--- 评测 {len(cases)} 案 × {arms}，数据版本 {version}")
        for case in cases:
            for arm in arms:
                t0 = time.time()
                try:
                    out = await (
                        bench.baseline(case, args.timeout)
                        if arm == "baseline"
                        else bench.pack(case, Decimal(args.budget), args.timeout)
                    )
                except Exception as exc:  # noqa: BLE001
                    out = ArmOutput(
                        case.case_id,
                        arm,
                        state="ERROR",
                        error=f"{type(exc).__name__}: {exc}"[:300],
                    )
                v = judge(case, out, store)
                verdicts.append(v)
                raw.append(
                    {
                        "output": {**out.__dict__, "cost": str(out.cost)},
                        "verdict": v.as_dict(),
                        "seconds": int(time.time() - t0),
                    }
                )
                print(
                    f"  {case.case_id:<40} {arm:<9} quality={v.quality:<11} citation={v.citation:<11} cost={v.cost} sqls={len(out.sqls)} {int(time.time() - t0)}s {'; '.join(v.reasons)[:160]}",
                    flush=True,
                )
    finally:
        for link in runner.links.values():
            if link.client:
                await link.client.close()
        await engine.dispose()
        with sync.begin() as c:
            c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        sync.dispose()

    summaries = summarize(verdicts)
    gate = (
        release_gate(summaries["baseline"], summaries["pack"])
        if {"baseline", "pack"} <= set(summaries)
        else None
    )
    meta = {
        "dataset_version": version,
        "cases": len(cases),
        "arms": ",".join(arms),
        "profile": "DATA_QUERY",
        "pack_version": "1",
        "run_at": ts,
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    (out_dir / f"{ts}-data_query.json").write_text(  # noqa: ASYNC240
        json.dumps(
            {
                "meta": meta,
                "summaries": {k: v.as_dict() for k, v in summaries.items()},
                "gate": gate.__dict__ if gate else None,
                "cases": raw,
            },
            ensure_ascii=False,
            indent=1,
            default=str,
        )
    )
    (out_dir / f"{ts}-data_query.md").write_text(  # noqa: ASYNC240
        render_markdown("问数二十题：专家包发布门", verdicts, summaries, gate, meta)
    )
    for s_ in summaries.values():
        print(
            f"  {s_.arm:<9} quality {s_.quality_pass}/{s_.n} citation {s_.citation_pass}/{s_.n} undecidable {s_.undecidable} cost {s_.cost_total}"
        )
    if gate:
        print(f"发布门：{gate.level} — {gate.reason}")
    print(f"报告：{out_dir}/{ts}-data_query.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
