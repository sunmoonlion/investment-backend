"""工作区与项目（PRD/apps/investment.md 第二、七节）。只管账；用户机器上的目录由 Codex 去碰。"""

from __future__ import annotations

from typing import Any

from app.application.ports.workbench import WorkbenchStore
from app.domain.workbench.errors import (
    ProjectArchived,
    ProjectBusy,
    RootOutsideWhitelist,
)
from app.domain.workbench.projects import (
    clean_relative_path,
    clean_title,
    default_project_title,
    project_dir,
    split_under_roots,
)

ONLINE = "online"


def plain(row: dict[str, Any]) -> dict[str, Any]:
    """账里取出来的一行，变成能直接给接口的样子。"""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if hasattr(value, "hex") and not isinstance(value, (bytes, str)):
            out[key] = str(value)
        else:
            out[key] = value
    return out


class ProjectService:
    def __init__(self, repo: WorkbenchStore):
        self.repo = repo

    async def workspaces(self, *, owner_actor_id: str) -> list[dict[str, Any]]:
        """我的工作区：每台机器的每个根目录。增减在用户机器上的本地代理里做，这里只读。"""
        found = []
        for env in await self.repo.list_environments(owner_actor_id=owner_actor_id):
            for root in env.get("roots") or []:
                found.append(
                    {
                        "environment_id": str(env["id"]),
                        "environment_name": env["name"],
                        "online": env.get("status") == ONLINE,
                        "root": root,
                    }
                )
        return found

    def _describe(
        self, project: dict[str, Any], env: dict[str, Any] | None
    ) -> dict[str, Any]:
        row = plain(project)
        row["directory"] = project_dir(project["workspace_root"], project["path"])
        row["archived"] = project.get("archived_at") is not None
        if env is not None:
            row["environment_name"] = env["name"]
            row["online"] = env.get("status") == ONLINE
        return row

    async def create(
        self,
        *,
        owner_actor_id: str,
        environment_id: str,
        workspace_root: str,
        path: str | None,
        title: str | None = None,
    ) -> dict[str, Any]:
        relative = clean_relative_path(path)
        async with self.repo.transaction():
            env = await self.repo.get_environment(
                environment_id, owner_actor_id=owner_actor_id
            )
            if workspace_root not in list(env.get("roots") or []):
                raise RootOutsideWhitelist(
                    "this workspace is not one of the machine's whitelisted roots",
                    workspace_root=workspace_root,
                )
            project_id = await self.repo.create_project(
                owner_actor_id=owner_actor_id,
                environment_id=environment_id,
                workspace_root=workspace_root,
                path=relative,
                title=clean_title(title)
                or default_project_title(relative, workspace_root),
            )
            return self._describe(await self.repo.get_project(project_id), env)

    async def ensure_for_directory(
        self, *, owner_actor_id: str, environment_id: str, directory: str
    ) -> dict[str, Any]:
        """按完整目录找项目，没有就建。给仍按完整目录建会话的旧入口用。"""
        env = await self.repo.get_environment(
            environment_id, owner_actor_id=owner_actor_id
        )
        roots: list[str] = list(env.get("roots") or [])
        found = split_under_roots(directory, roots)
        if found is None:
            if roots:
                raise RootOutsideWhitelist(
                    "project root is outside the environment's whitelisted roots",
                    project_root=directory,
                )
            found = (directory, "")  # 这台机器没有登记白名单：目录自己当工作区
        root, relative = found
        existing = await self.repo.find_project(
            owner_actor_id=owner_actor_id,
            environment_id=environment_id,
            workspace_root=root,
            path=relative,
        )
        if existing is not None:
            return existing
        project_id = await self.repo.create_project(
            owner_actor_id=owner_actor_id,
            environment_id=environment_id,
            workspace_root=root,
            path=relative,
            title=default_project_title(relative, root),
        )
        return await self.repo.get_project(project_id)

    async def listing(
        self, *, owner_actor_id: str, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        envs = {
            str(env["id"]): env
            for env in await self.repo.list_environments(owner_actor_id=owner_actor_id)
        }
        return [
            self._describe(project, envs.get(str(project["environment_id"])))
            for project in await self.repo.list_projects(
                owner_actor_id=owner_actor_id, include_archived=include_archived
            )
        ]

    async def view(self, project_id: str, *, owner_actor_id: str) -> dict[str, Any]:
        """一个项目，连同它的对话与委托的摘要。"""
        project = await self.repo.get_project(project_id, owner_actor_id=owner_actor_id)
        env = await self.repo.get_environment(str(project["environment_id"]))
        active = await self.repo.active_task_of_project(str(project["id"]))
        sessions = await self.repo.list_sessions(
            owner_actor_id=owner_actor_id, project_id=str(project["id"])
        )
        tasks = await self.repo.list_tasks(
            owner_actor_id=owner_actor_id, project_id=str(project["id"])
        )
        return {
            "project": self._describe(project, env),
            "active_task_id": str(active["id"]) if active else None,
            "conversations": [
                {
                    "id": str(s["id"]),
                    "kind": s["kind"],
                    "title": s["title"],
                    "wheel": s["wheel"],
                    "active_task_id": (
                        str(s["active_task_id"]) if s["active_task_id"] else None
                    ),
                    "last_active_at": s["last_active_at"],
                }
                for s in sessions
            ],
            "tasks": [
                {
                    "id": str(t["id"]),
                    "session_id": str(t["session_id"]),
                    "profile_id": t["profile_id"],
                    "state": t["state"],
                    "question": _question(t),
                    "budget": t["budget"],
                    "created_at": t["created_at"],
                }
                for t in tasks
            ],
        }

    async def rename(
        self, project_id: str, *, owner_actor_id: str, title: str
    ) -> dict[str, Any]:
        cleaned = clean_title(title)
        if cleaned is None:
            raise ValueError("title must not be empty")
        async with self.repo.transaction():
            await self.repo.get_project(
                project_id, owner_actor_id=owner_actor_id, for_update=True
            )
            await self.repo.update_project(project_id, title=cleaned)
        return (await self.view(project_id, owner_actor_id=owner_actor_id))["project"]

    async def archive(
        self, project_id: str, *, owner_actor_id: str, archived: bool
    ) -> dict[str, Any]:
        async with self.repo.transaction():
            await self.repo.get_project(
                project_id, owner_actor_id=owner_actor_id, for_update=True
            )
            if archived and await self.repo.active_task_of_project(project_id):
                raise ProjectBusy(
                    "the expert is still working in this project; it cannot be archived now"
                )
            await self.repo.update_project(project_id, archived=archived)
        return (await self.view(project_id, owner_actor_id=owner_actor_id))["project"]

    async def usable(self, project_id: str, *, owner_actor_id: str) -> dict[str, Any]:
        """要在里面开对话、请专家的项目：必须是自己的、没有归档的。"""
        project = await self.repo.get_project(project_id, owner_actor_id=owner_actor_id)
        if project.get("archived_at") is not None:
            raise ProjectArchived("this project is archived; restore it first")
        return project


def _question(task: dict[str, Any]) -> str | None:
    original = task.get("original_input") or {}
    given = original.get("input") if isinstance(original, dict) else None
    if isinstance(given, dict):
        text = given.get("text") or given.get("question")
        return str(text) if text else None
    return str(given) if given else None
