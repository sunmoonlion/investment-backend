"""runner：第五个进程角色。对每个沙箱保持一条到 app-server 的连接；把通知投影成 Session 事件；
执行网页接口排进 workbench_commands 的命令；把 app-server 的审批请求按方向盘路由。

它不做业务判断：状态、方向盘、Interaction 全经账房（Ledger / SessionService）；这里只是连接与搬运。
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.workbench.states import Wheel
from app.infrastructure.workbench.app_server_client import (
    AppServerClient,
    AppServerError,
    resolve_token_ref,
)
from app.infrastructure.workbench.repository import WorkbenchRepository

log = logging.getLogger(__name__)

# 不落库的流式通知（条目级事件才进 Session 事件，persistence.md）
_SKIP_SUFFIXES = ("/delta", "/outputDelta", "/reasoningDelta", "/textDelta")
_KIND_BY_PREFIX = (
    ("thread/environment/", "environment"),
    ("thread/tokenUsage", "usage"),
    ("thread/", "thread"),
    ("turn/", "turn"),
    ("item/", "item"),
    ("error", "error"),
)


def event_kind(method: str) -> str:
    for prefix, kind in _KIND_BY_PREFIX:
        if method.startswith(prefix):
            return kind
    return "other"


def thread_id_of(params: dict[str, Any]) -> str | None:
    for path in (
        ("threadId",),
        ("thread", "id"),
        ("turn", "threadId"),
        ("item", "threadId"),
    ):
        cur: Any = params
        for key in path:
            cur = cur.get(key) if isinstance(cur, dict) else None
            if cur is None:
                break
        if isinstance(cur, str):
            return cur
    return None


class Publisher:
    """Redis 发布；测试里可以传 None。"""

    def __init__(self, redis, prefix: str):
        self.redis = redis
        self.prefix = prefix

    def session_channel(self, session_id: str) -> str:
        return f"{self.prefix}:session:{session_id}:events"

    def commands_channel(self) -> str:
        return f"{self.prefix}:commands"

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.publish(
                channel, json.dumps(payload, ensure_ascii=False, default=str)
            )
        except Exception:  # noqa: BLE001
            log.warning("redis publish failed channel=%s", channel)


class SandboxLink:
    """一个沙箱一条连接。"""

    def __init__(self, runner: Runner, sandbox: dict[str, Any]):
        self.runner = runner
        self.sandbox = sandbox
        self.sandbox_id = str(sandbox["id"])
        self.client: AppServerClient | None = None
        self.threads: dict[str, str] = {}  # thread_id -> session_id
        self.pending_approvals: dict[
            str, asyncio.Future
        ] = {}  # request_id -> future(decision)
        self._lock = asyncio.Lock()

    async def ensure_connected(self) -> AppServerClient:
        async with self._lock:
            if self.client is not None and self.client.connected:
                return self.client
            token = resolve_token_ref(self.sandbox["token_ref"])
            client = AppServerClient(
                self.sandbox["app_server_url"],
                token,
                on_notification=self.on_notification,
                on_server_request=self.on_server_request,
            )
            await client.connect()
            self.client = client
            log.info(
                "sandbox link up sandbox=%s url=%s",
                self.sandbox_id,
                self.sandbox["app_server_url"],
            )
            return client

    async def session_for_thread(
        self, thread_id: str | None, repo: WorkbenchRepository
    ) -> str | None:
        if thread_id is None:
            return None
        sid = self.threads.get(thread_id)
        if sid is None:
            row = await repo.get_session_by_thread(thread_id)
            if row is not None:
                sid = str(row["id"])
                self.threads[thread_id] = sid
        return sid

    # ---- 通知 → Session 事件 ----
    async def on_notification(self, method: str, params: dict[str, Any]) -> None:
        if method.endswith(_SKIP_SUFFIXES):
            return
        async with self.runner.session_factory() as s:
            repo = WorkbenchRepository(s)
            session_id = await self.session_for_thread(thread_id_of(params), repo)
            if session_id is None:
                log.debug("notification without session method=%s", method)
                return
            async with repo.transaction():
                event = await repo.append_event(
                    session_id=session_id,
                    kind=event_kind(method),
                    event_type=method,
                    payload=params,
                )
            await self.runner.publisher.publish(
                self.runner.publisher.session_channel(session_id), event
            )

    # ---- 服务端请求（审批）→ 按方向盘路由 ----
    async def on_server_request(
        self, rid: Any, method: str, params: dict[str, Any]
    ) -> None:
        client = self.client
        assert client is not None
        if not method.endswith("requestApproval"):
            await client.respond_error(
                rid, -32601, f"workbench does not handle {method}"
            )
            return
        request_id = f"{self.sandbox_id}:{rid}"
        async with self.runner.session_factory() as s:
            repo = WorkbenchRepository(s)
            session_id = await self.session_for_thread(thread_id_of(params), repo)
            if session_id is None:
                await client.respond(rid, {"decision": "decline"})
                return
            session = await repo.get_session(session_id)
            summary = {
                "method": method,
                "command": params.get("command"),
                "cwd": params.get("cwd"),
                "reason": params.get("reason"),
                "itemId": params.get("itemId"),
                "environmentId": params.get("environmentId"),
            }
            if session["wheel"] == Wheel.advisor:
                # 顾问驾驶：第一期策略——项目目录内的写与只读命令由专家包 auto_allow 决定；这一片先一律拒绝并留痕
                async with repo.transaction():
                    await repo.log_approval(
                        session_id=session_id,
                        task_id=str(session["active_task_id"])
                        if session["active_task_id"]
                        else None,
                        request_id=request_id,
                        method=method,
                        summary=summary,
                        decision="decline",
                        source="policy",
                    )
                await client.respond(rid, {"decision": "decline"})
                return
            # 用户自驾：开一个 tool_approval Interaction，等用户在网页上答
            token = secrets.token_urlsafe(32)
            async with repo.transaction():
                iid = await repo.insert_interaction(
                    session_id=session_id,
                    task_id=None,
                    attempt_id=None,
                    kind="tool_approval",
                    prompt={
                        "title": "命令需要你批准",
                        "question": params.get("reason")
                        or "Codex 请求执行超出沙箱的动作",
                        "options": [
                            {"id": "accept", "label": "允许一次"},
                            {"id": "acceptForSession", "label": "本会话都允许"},
                            {"id": "decline", "label": "拒绝"},
                        ],
                        "subject": {"request_id": request_id, **summary},
                        "evidence": [],
                        "unknowns": [],
                    },
                    subject_digest=None,
                    token=token,
                    target_state_version=None,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
                event = await repo.append_event(
                    session_id=session_id,
                    kind="approval",
                    event_type="interaction/opened",
                    payload={
                        "interaction_id": iid,
                        "kind": "tool_approval",
                        "token": token,
                        "subject": summary,
                    },
                )
            await self.runner.publisher.publish(
                self.runner.publisher.session_channel(session_id), event
            )
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending_approvals[request_id] = fut
        try:
            decision = await asyncio.wait_for(fut, timeout=3600)
        except TimeoutError:
            decision = "decline"
        finally:
            self.pending_approvals.pop(request_id, None)
        async with self.runner.session_factory() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                await repo.log_approval(
                    session_id=session_id,
                    task_id=None,
                    request_id=request_id,
                    method=method,
                    summary=summary,
                    decision=decision,
                    source="user",
                )
        await client.respond(rid, {"decision": decision})

    def resolve_approval(self, request_id: str, decision: str) -> bool:
        fut = self.pending_approvals.get(request_id)
        if fut is None or fut.done():
            return False
        fut.set_result(decision)
        return True


class Runner:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        publisher: Publisher,
        runner_id: str,
        environment_key: str = "user-pc",
        poll_seconds: float = 1.0,
    ):
        self.session_factory = session_factory
        self.publisher = publisher
        self.runner_id = runner_id
        self.environment_key = environment_key
        self.poll_seconds = poll_seconds
        self.links: dict[str, SandboxLink] = {}
        self._stop = asyncio.Event()
        self.handled = 0

    async def link_for(self, sandbox_id: str, repo: WorkbenchRepository) -> SandboxLink:
        link = self.links.get(sandbox_id)
        if link is None:
            link = SandboxLink(self, await repo.get_sandbox(sandbox_id))
            self.links[sandbox_id] = link
        return link

    async def run_forever(self) -> None:
        log.info("workbench runner starting id=%s", self.runner_id)
        while not self._stop.is_set():
            try:
                n = await self.run_once()
            except Exception:  # noqa: BLE001
                log.exception("runner iteration failed")
                n = 0
            if n == 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
                except TimeoutError:
                    pass
        for link in self.links.values():
            if link.client:
                await link.client.close()

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self) -> int:
        async with self.session_factory() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                await repo.requeue_stale_commands()
                commands = await repo.claim_commands(claimed_by=self.runner_id)
        for cmd in commands:
            error: str | None = None
            try:
                await self.handle(cmd)
            except Exception as exc:  # noqa: BLE001
                log.exception("command failed id=%s kind=%s", cmd["id"], cmd["kind"])
                error = f"{type(exc).__name__}: {exc}"[:2000]
            async with self.session_factory() as s:
                repo = WorkbenchRepository(s)
                async with repo.transaction():
                    await repo.finish_command(cmd["id"], error=error)
                    if error:
                        ev = await repo.append_event(
                            session_id=cmd["session_id"],
                            kind="error",
                            event_type="command/failed",
                            payload={
                                "command_id": cmd["id"],
                                "kind": cmd["kind"],
                                "error": error,
                            },
                        )
                        await self.publisher.publish(
                            self.publisher.session_channel(cmd["session_id"]), ev
                        )
            self.handled += 1
        return len(commands)

    # ---- 命令 ----
    async def handle(self, cmd: dict[str, Any]) -> None:
        kind, payload, session_id = cmd["kind"], cmd["payload"], cmd["session_id"]
        async with self.session_factory() as s:
            repo = WorkbenchRepository(s)
            session = await repo.get_session(session_id)
            link = await self.link_for(str(session["sandbox_id"]), repo)
        if kind == "approval.respond":
            ok = link.resolve_approval(payload["request_id"], payload["decision"])
            if not ok:
                raise RuntimeError(
                    "no pending approval for this request (runner restarted or already answered)"
                )
            return
        client = await link.ensure_connected()
        if kind == "session.start_thread" or (
            kind == "turn.start" and session["thread_id"] is None
        ):
            await self._start_thread(link, client, session)
            if kind == "session.start_thread":
                return
            async with self.session_factory() as s:
                session = await WorkbenchRepository(s).get_session(session_id)
        if kind == "turn.start":
            if session["wheel"] != payload.get("by", "user"):
                raise RuntimeError(
                    f"wheel is held by {session['wheel']}; turn by {payload.get('by', 'user')} refused"
                )
            result = await client.request(
                "turn/start",
                {
                    "threadId": session["thread_id"],
                    "input": [{"type": "text", "text": payload["text"]}],
                    "clientUserMessageId": payload.get("request_id"),
                },
                timeout=120,
            )
            async with self.session_factory() as s:
                repo = WorkbenchRepository(s)
                async with repo.transaction():
                    ev = await repo.append_event(
                        session_id=session_id,
                        kind="turn",
                        event_type="turn/accepted",
                        payload={
                            "request_id": payload.get("request_id"),
                            "by": payload.get("by", "user"),
                            "turn": (result or {}).get("turn"),
                        },
                    )
                await self.publisher.publish(
                    self.publisher.session_channel(session_id), ev
                )
            return
        if kind == "turn.interrupt":
            await client.request(
                "turn/interrupt",
                {"threadId": session["thread_id"], "turnId": payload.get("turn_id")},
                timeout=30,
            )
            return
        raise RuntimeError(f"unknown command kind {kind}")

    async def _start_thread(
        self, link: SandboxLink, client: AppServerClient, session: dict[str, Any]
    ) -> None:
        settings = dict(session.get("thread_settings") or {})
        env_key = settings.pop("environmentId", self.environment_key)
        params = {
            "cwd": session["project_root"],
            "environments": [
                {"environmentId": env_key, "cwd": session["project_root"]}
            ],
            **settings,
        }
        try:
            result = await client.request("thread/start", params, timeout=60)
        except AppServerError as exc:
            raise RuntimeError(f"thread/start rejected: {exc}") from exc
        thread_id = ((result or {}).get("thread") or {}).get("id")
        if not thread_id:
            raise RuntimeError(f"thread/start returned no thread id: {result}")
        link.threads[thread_id] = str(session["id"])
        async with self.session_factory() as s:
            repo = WorkbenchRepository(s)
            async with repo.transaction():
                await repo.set_session_thread(str(session["id"]), thread_id)
                ev = await repo.append_event(
                    session_id=str(session["id"]),
                    kind="thread",
                    event_type="session/thread_started",
                    payload={"thread_id": thread_id, "environment": env_key},
                )
            await self.publisher.publish(
                self.publisher.session_channel(str(session["id"])), ev
            )
