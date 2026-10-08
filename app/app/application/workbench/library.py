"""知识库（SDD 0011 第一期）：用户的底稿与交回物，列出来、读、改名、拿掉。

清单是算出来的：这个人全部委托里的交回物（`workbench_artifacts`，kind=step）按委托和名字归成
一份份资料，每个委托再归出一份底稿。用户的改名、拿掉记在一张小表里盖在上面。
只经两条路读：网页的登录态，和沙箱按用户签的 MCP 令牌；两条路都带 owner_actor_id。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.application.ports.workbench import WorkbenchStore
from app.application.workbench.expert_desk import ExpertDesk, question_of
from app.domain.workbench.errors import NotFound
from app.domain.workbench.library import (
    KIND_DELIVERABLE,
    KIND_DOSSIER,
    as_text,
    citation,
    default_deliverable_title,
    default_dossier_title,
    deliverable_id,
    dossier_id,
    page_of,
    parse_item_id,
    sha256_of,
)
from app.domain.workbench.packs import find_pack
from app.domain.workbench.states import TASK_TERMINAL, TaskState


class Library:
    def __init__(self, repo: WorkbenchStore, *, owner_actor_id: str) -> None:
        self.repo = repo
        self.owner = owner_actor_id

    # ---------------- 清单 ----------------
    async def listing(
        self,
        *,
        kind: str | None = None,
        project_id: str | None = None,
        q: str | None = None,
        include_deleted: bool = False,
    ) -> list[dict[str, Any]]:
        rows = await self.repo.list_owner_step_artifacts(owner_actor_id=self.owner)
        overlay = await self.repo.library_overlay(owner_actor_id=self.owner)
        by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_task[str(row["task_id"])].append(row)
        items: list[dict[str, Any]] = []
        for task_id, parts in by_task.items():
            first = parts[0]
            # 还在做的委托不进知识库：交回了才算资料
            if TaskState(first["state"]) not in TASK_TERMINAL:
                continue
            pack = find_pack(first["profile_id"], first["profile_version"])
            expert = (pack.name or pack.title) if pack else ""
            steps = [a for a in parts if a["kind"] == "step"]
            drafts = [a for a in parts if a["kind"] == "user_draft"]
            if steps:
                items.append(
                    self._item(
                        dossier_id(task_id),
                        KIND_DOSSIER,
                        first,
                        title=default_dossier_title(expert, question_of(first)),
                        versions=1 + len(drafts),
                        updated_at=max(a["created_at"] for a in parts),
                        size_bytes=None,
                        source={"expert": expert, "question": question_of(first)},
                        overlay=overlay,
                    )
                )
            by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for a in steps:
                by_name[a["name"]].append(a)
            for name, versions in by_name.items():
                latest = max(versions, key=lambda a: a["version"])
                step = None
                if pack is not None:
                    step = next(
                        (s for s in pack.workflow if s.output_artifact == name), None
                    )
                items.append(
                    self._item(
                        deliverable_id(task_id, name),
                        KIND_DELIVERABLE,
                        first,
                        title=default_deliverable_title(
                            step.title if step else "", name, expert
                        ),
                        versions=len(versions),
                        updated_at=latest["created_at"],
                        size_bytes=latest["size_bytes"],
                        source={
                            "expert": expert,
                            "question": question_of(first),
                            "step_title": step.title if step else None,
                            "artifact": name,
                        },
                        overlay=overlay,
                    )
                )
        chosen = [
            i
            for i in items
            if (include_deleted or not i["deleted"])
            and (kind is None or i["kind"] == kind)
            and (project_id is None or i["project_id"] == project_id)
            and (not q or q.strip().lower() in i["title"].lower())
        ]
        chosen.sort(key=lambda i: i["updated_at"], reverse=True)
        return chosen

    def _item(
        self,
        item_id: str,
        kind: str,
        task_row: dict[str, Any],
        *,
        title: str,
        versions: int,
        updated_at: Any,
        size_bytes: int | None,
        source: dict[str, Any],
        overlay: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        mine = overlay.get(item_id) or {}
        return {
            "id": item_id,
            "kind": kind,
            "title": mine.get("title") or title,
            "default_title": title,
            "project_id": (
                str(task_row["project_id"]) if task_row.get("project_id") else None
            ),
            "task_id": str(task_row["task_id"]),
            "session_id": str(task_row["session_id"]),
            "versions": versions,
            "updated_at": updated_at,
            "created_at": task_row["task_created_at"],
            "size_bytes": size_bytes,
            "source": source,
            "deleted": mine.get("deleted_at") is not None,
        }

    # ---------------- 一份资料 ----------------
    async def item(self, item_id: Any) -> dict[str, Any]:
        parsed = parse_item_id(item_id)
        if parsed is None:
            raise NotFound("library item not found")
        found = next((i for i in await self.listing() if i["id"] == item_id), None)
        if found is None:
            raise NotFound("library item not found")
        kind, task_id, name = parsed
        versions: list[dict[str, Any]] = []
        if kind == KIND_DELIVERABLE:
            for a in await self.repo.list_artifacts(task_id):
                if a["name"] == name and a["kind"] == "step":
                    versions.append(
                        {
                            "version": a["version"],
                            "created_at": a["created_at"],
                            "sha256": a["digest"],
                        }
                    )
        else:
            drafts = [
                a
                for a in await self.repo.list_artifacts(task_id)
                if a["name"] == "conclusion" and a["kind"] == "user_draft"
            ]
            versions.append(
                {"version": 1, "created_at": found["created_at"], "note": "专家交回"}
            )
            for n, a in enumerate(sorted(drafts, key=lambda a: a["version"]), start=2):
                versions.append(
                    {"version": n, "created_at": a["created_at"], "note": "你填了结论"}
                )
        return {**found, "version_list": versions}

    async def content(self, item_id: Any, version: int | None = None) -> dict[str, Any]:
        """一份资料的某个版本。底稿给整份 Markdown；交回物给它的内容。"""
        found = await self.item(item_id)
        kind, task_id, name = parse_item_id(item_id)  # type: ignore[misc]
        if kind == KIND_DELIVERABLE:
            if name is None:
                raise NotFound("library item version not found")
            art = await self.repo.get_artifact(
                task_id=task_id, name=name, version=version
            )
            if art is None or art["kind"] != "step":
                raise NotFound("library item version not found")
            text = as_text(art["content"])
            return {
                "item": found,
                "version": art["version"],
                "sha256": art["digest"],
                "kind": kind,
                "content": art["content"],
                "text": text,
            }
        wanted = version or found["versions"]
        if wanted < 1 or wanted > found["versions"]:
            raise NotFound("library item version not found")
        text = await ExpertDesk(self.repo).dossier_export(
            task_id, owner_actor_id=self.owner
        )
        return {
            "item": found,
            "version": found["versions"],
            "sha256": sha256_of(text),
            "kind": kind,
            "content": None,
            "text": text,
        }

    async def rename(self, item_id: Any, title: str) -> dict[str, Any]:
        found = await self.item(item_id)
        async with self.repo.transaction():
            await self.repo.put_library_overlay(
                owner_actor_id=self.owner, item_id=found["id"], title=title.strip()
            )
        return await self.item(item_id)

    async def remove(self, item_id: Any) -> None:
        found = await self.item(item_id)
        async with self.repo.transaction():
            await self.repo.put_library_overlay(
                owner_actor_id=self.owner, item_id=found["id"], deleted=True
            )

    # ---------------- 给 Codex 的两个工具 ----------------
    async def tool_list(self, *, kind: str | None = None, q: str | None = None) -> dict:
        return {
            "items": [
                {
                    "item": i["id"],
                    "kind": i["kind"],
                    "title": i["title"],
                    "project": i["project_id"],
                    "versions": i["versions"],
                    "updated_at": i["updated_at"],
                    "expert": i["source"].get("expert"),
                    "question": i["source"].get("question"),
                }
                for i in await self.listing(kind=kind, q=q)
            ]
        }

    async def tool_read(
        self, item_id: Any, *, version: int | None = None, page: int = 1
    ) -> dict[str, Any]:
        got = await self.content(item_id, version)
        paged = page_of(got["text"], page)
        return {
            "item": got["item"]["id"],
            "title": got["item"]["title"],
            "kind": got["kind"],
            **paged,
            "citation": citation(got["item"]["id"], got["version"], got["sha256"]),
        }
