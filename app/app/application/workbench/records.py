"""专家读本项目里别的对话与底稿（PRD/apps/investment.md 7.4、F-PROJ-06）。

只读。只在专家处理期间开着：每次调用都按委托重新核对——委托是这个用户的、还没结束、
方向盘在专家手里。能读的范围是委托所在的那一个项目，读不到别的项目、别的用户。
专家读了什么，记在账里。
"""

from __future__ import annotations

import uuid
from typing import Any

from app.application.ports.workbench import WorkbenchStore
from app.application.workbench.expert_desk import ExpertDesk, question_of
from app.domain.workbench.errors import NotFound, RecordsRefused
from app.domain.workbench.packs import find_pack
from app.domain.workbench.records import (
    DOSSIER_CHARS,
    FIRST_CHARS,
    clip,
    one_line,
    page_of,
    record_of,
)
from app.domain.workbench.states import TASK_TERMINAL, Wheel
from app.domain.workbench.step_view import TERMINAL_WORDS

RECORD_EVENTS = (
    "turn/requested",
    "item/completed",
    "task/received",
    "task/state",
    "step/accepted",
    "step/rejected",
    "interaction/opened",
)


def as_id(value: Any, what: str) -> str:
    """模型给的编号。不是编号的样子就按找不到答，不让它走到数据库里去。"""
    try:
        return str(uuid.UUID(str(value).strip()))
    except (ValueError, AttributeError):
        raise NotFound(f"{what} not found") from None


class ProjectRecords:
    def __init__(self, repo: WorkbenchStore, *, owner_actor_id: str):
        self.repo = repo
        self.owner = owner_actor_id

    async def _working(self, task: Any) -> dict[str, Any]:
        """这次调用凭的是哪个委托。返回委托；不该读的时候拒绝。"""
        found = await self.repo.get_task(as_id(task, "task"), owner_actor_id=self.owner)
        if found["state"] in TASK_TERMINAL or not found.get("project_id"):
            raise RecordsRefused("the expert is not working on this task now")
        session = await self.repo.get_session(str(found["session_id"]))
        if session["wheel"] != Wheel.advisor or str(session["active_task_id"]) != str(
            found["id"]
        ):
            raise RecordsRefused("the expert is not working on this task now")
        return found

    async def _noted(self, task: dict[str, Any], tool: str, **what: Any) -> None:
        async with self.repo.transaction():
            await self.repo.append_event(
                session_id=str(task["session_id"]),
                kind="records",
                event_type="records/read",
                payload={"tool": tool, **what},
                task_id=str(task["id"]),
            )

    async def list_conversations(self, task: Any) -> dict[str, Any]:
        working = await self._working(task)
        rows = await self.repo.conversation_digests(
            project_id=str(working["project_id"]), owner_actor_id=self.owner
        )
        await self._noted(working, "list_project_conversations")
        return {
            "conversations": [
                {
                    "conversation": str(row["id"]),
                    "kind": row["kind"],
                    "title": row["title"],
                    "started_at": row["created_at"],
                    "last_active_at": row["last_active_at"],
                    "turns": int(row["turns"]),
                    "first_message": one_line(row["first_message"], FIRST_CHARS),
                    # 当前这段对话就在线里，不用经工具读
                    "current": str(row["id"]) == str(working["session_id"]),
                }
                for row in rows
            ]
        }

    async def read_conversation(
        self, task: Any, conversation: Any, page: Any = 1
    ) -> dict[str, Any]:
        working = await self._working(task)
        session = await self.repo.get_session(
            as_id(conversation, "conversation"), owner_actor_id=self.owner
        )
        if str(session.get("project_id")) != str(working["project_id"]):
            # 别的项目的对话：和不存在一样答
            raise NotFound("conversation not found")
        if str(session["id"]) == str(working["session_id"]):
            raise RecordsRefused(
                "this is the current conversation; it is already in your context"
            )
        events = await self.repo.list_record_events(
            session_id=str(session["id"]), types=RECORD_EVENTS
        )
        shown = page_of(record_of(events), page)
        await self._noted(
            working,
            "read_project_conversation",
            conversation=str(session["id"]),
            page=shown["page"],
        )
        return {
            "conversation": str(session["id"]),
            "kind": session["kind"],
            "title": session["title"],
            **shown,
        }

    async def _dossiers(self, working: dict[str, Any]) -> list[dict[str, Any]]:
        return await self.repo.list_tasks(
            owner_actor_id=self.owner, project_id=str(working["project_id"])
        )

    async def list_dossiers(self, task: Any) -> dict[str, Any]:
        working = await self._working(task)
        found = []
        for row in reversed(await self._dossiers(working)):
            pack = find_pack(row["profile_id"], row["profile_version"])
            found.append(
                {
                    "dossier": str(row["id"]),
                    "expert": (pack.name or pack.title) if pack else "",
                    "state": TERMINAL_WORDS.get(row["state"], "进行中"),
                    "asked_at": row["created_at"],
                    "question": one_line(question_of(row), FIRST_CHARS),
                    "current": str(row["id"]) == str(working["id"]),
                }
            )
        await self._noted(working, "list_project_dossiers")
        return {"dossiers": found}

    async def read_dossier(self, task: Any, dossier: Any) -> dict[str, Any]:
        working = await self._working(task)
        wanted = as_id(dossier, "dossier")
        if wanted not in {str(row["id"]) for row in await self._dossiers(working)}:
            raise NotFound("dossier not found")
        text = await ExpertDesk(self.repo).dossier_export(
            wanted, owner_actor_id=self.owner
        )
        await self._noted(working, "read_project_dossier", dossier=wanted)
        return {
            "dossier": wanted,
            "text": clip(text, DOSSIER_CHARS),
            "truncated": len(text) > DOSSIER_CHARS,
        }
