"""会话与方向盘：建 Session、谁能发 turn、记事件。与 app-server 的往来由 runner 里的客户端做，这里只管账。"""

from __future__ import annotations

from typing import Any

from app.domain.workbench.errors import RootOutsideWhitelist, WheelHeldByOther
from app.domain.workbench.states import Wheel
from app.infrastructure.workbench.repository import WorkbenchRepository


class SessionService:
    def __init__(self, repo: WorkbenchRepository):
        self.repo = repo

    async def create(
        self,
        *,
        owner_actor_id: str,
        environment_id: str,
        sandbox_id: str,
        project_root: str,
        thread_settings: dict | None = None,
    ) -> dict[str, Any]:
        async with self.repo.transaction():
            env = await self.repo.get_environment(
                environment_id, owner_actor_id=owner_actor_id
            )
            await self.repo.get_sandbox(sandbox_id, owner_actor_id=owner_actor_id)
            roots: list[str] = list(env.get("roots") or [])
            if roots and not any(
                project_root == r or project_root.startswith(r.rstrip("/") + "/")
                for r in roots
            ):
                raise RootOutsideWhitelist(
                    "project root is outside the environment's whitelisted roots",
                    project_root=project_root,
                )
            sid = await self.repo.create_session(
                owner_actor_id=owner_actor_id,
                environment_id=environment_id,
                sandbox_id=sandbox_id,
                project_root=project_root,
                thread_settings=thread_settings
                or {"approvalPolicy": "on-request", "sandbox": "workspace-write"},
            )
            await self.repo.append_event(
                session_id=sid,
                kind="session",
                event_type="session/created",
                payload={
                    "environment_id": environment_id,
                    "sandbox_id": sandbox_id,
                    "project_root": project_root,
                },
            )
            return {"session_id": sid}

    async def assert_user_may_drive(
        self, session_id: str, *, owner_actor_id: str
    ) -> dict[str, Any]:
        """用户发 turn 前的门（F-WHEEL-02）。"""
        session = await self.repo.get_session(session_id, owner_actor_id=owner_actor_id)
        if session["wheel"] != Wheel.user:
            raise WheelHeldByOther(
                "the advisor holds the wheel; you can watch, answer interactions or cancel",
                session_id=session_id,
                active_task_id=str(session["active_task_id"]),
            )
        return session

    async def assert_advisor_may_drive(
        self, session_id: str, *, task_id: str
    ) -> dict[str, Any]:
        session = await self.repo.get_session(session_id)
        if (
            session["wheel"] != Wheel.advisor
            or str(session["active_task_id"]) != task_id
        ):
            raise WheelHeldByOther("the user holds the wheel", session_id=session_id)
        return session

    async def record_user_turn_requested(
        self, session_id: str, *, owner_actor_id: str, text: str, request_id: str
    ) -> dict[str, Any]:
        """把用户的 turn 请求记成事件（runner 消费 Outbox 里的这条去发 turn/start）。"""
        async with self.repo.transaction():
            await self.assert_user_may_drive(session_id, owner_actor_id=owner_actor_id)
            await self.repo.touch_session(session_id)
            return await self.repo.append_event(
                session_id=session_id,
                kind="turn",
                event_type="turn/requested",
                payload={"request_id": request_id, "text": text, "by": "user"},
            )

    async def view(self, session_id: str, *, owner_actor_id: str) -> dict[str, Any]:
        s = await self.repo.get_session(session_id, owner_actor_id=owner_actor_id)
        pending = await self.repo.list_interactions(
            session_id=session_id, status="pending"
        )
        for d in (s, *pending):
            for k, v in list(d.items()):
                if hasattr(v, "hex") and not isinstance(v, (bytes, str)):
                    d[k] = str(v)
            d.pop("token_hash", None)
        return {"session": s, "pending_interactions": pending}
