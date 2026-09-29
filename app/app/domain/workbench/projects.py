"""工作区、项目、对话的种类（PRD/apps/investment.md 第二、七节）。不依赖框架、数据库与网络。

工作区是用户机器上的一个根目录；项目是它下面的一个子目录；对话分聊天与工作。
用户的机器可能是 Windows，也可能是 Linux、macOS：路径怎么拼，跟着工作区自己的写法走。
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from app.domain.workbench.errors import ProjectPathInvalid

MAX_TITLE_CHARS = 200
DEFAULT_TITLE_CHARS = 30
MAX_PATH_CHARS = 400
MAX_SEGMENT_CHARS = 120

_WINDOWS_ROOT = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")
_FORBIDDEN_IN_SEGMENT = re.compile(r'[<>:"|?*\\\x00-\x1f\x7f]')
_WINDOWS_RESERVED = re.compile(
    r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$", re.IGNORECASE
)


class ConversationKind(StrEnum):
    CHAT = "chat"  # 一问一答。不改文件，不跑命令
    WORK = "work"  # 代理在项目里动手做。必须属于一个项目


def is_windows_root(root: str) -> bool:
    return bool(_WINDOWS_ROOT.match(root))


def clean_relative_path(raw: str | None) -> str:
    """项目在工作区下的相对路径，用 `/` 分段。空串表示工作区本身。

    不许往上走、不许绝对路径、不许带盘符或别的系统上不能用的字符：
    同一条规则在三种系统上都成立，换一台机器项目还能对上。
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ProjectPathInvalid("project path must be text")
    text = raw.strip()
    if text in ("", "."):
        return ""
    if len(text) > MAX_PATH_CHARS:
        raise ProjectPathInvalid("project path is too long")
    if text.startswith(("/", "\\", "~")) or _WINDOWS_ROOT.match(text):
        raise ProjectPathInvalid("project path must be relative to the workspace")
    segments = text.rstrip("/").split("/")
    for segment in segments:
        if segment in ("", ".", ".."):
            raise ProjectPathInvalid("project path must not contain empty, . or ..")
        if len(segment) > MAX_SEGMENT_CHARS:
            raise ProjectPathInvalid("a folder name in the project path is too long")
        if _FORBIDDEN_IN_SEGMENT.search(segment):
            raise ProjectPathInvalid("project path contains a character not allowed")
        if segment != segment.strip() or segment.endswith("."):
            raise ProjectPathInvalid(
                "a folder name must not start or end with a space, or end with a dot"
            )
        if _WINDOWS_RESERVED.match(segment):
            raise ProjectPathInvalid("project path contains a reserved name")
    return "/".join(segments)


def project_dir(workspace_root: str, relative: str) -> str:
    """项目在用户机器上的完整目录。分隔符跟着工作区的写法走。"""
    if not relative:
        return workspace_root
    if is_windows_root(workspace_root):
        return workspace_root.rstrip("\\/") + "\\" + relative.replace("/", "\\")
    return workspace_root.rstrip("/") + "/" + relative


def split_under_roots(directory: str, roots: list[str]) -> tuple[str, str] | None:
    """已有的完整目录落在哪个工作区下：返回（工作区，相对路径）。落不进任何一个返回 None。

    有几个工作区都包含它时取最长的那个。用在迁移旧会话、和仍按完整目录建会话的旧入口。
    """
    best: tuple[str, str] | None = None
    for root in roots:
        windows = is_windows_root(root)
        trimmed = root.rstrip("\\/" if windows else "/") or root
        left, right = (
            (directory.lower(), trimmed.lower())
            if windows
            else (
                directory,
                trimmed,
            )
        )
        if left == right:
            relative = ""
        elif left.startswith(right) and directory[len(trimmed)] in (
            "\\/" if windows else "/"
        ):
            relative = directory[len(trimmed) + 1 :].replace("\\", "/").strip("/")
        else:
            continue
        if best is None or len(trimmed) > len(best[0].rstrip("\\/")):
            best = (root, relative)
    return best


def clean_title(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    if not text:
        return None
    return text[:MAX_TITLE_CHARS]


def default_title(first_message: str) -> str:
    """对话的名字默认取第一句话的开头。不为起名字去调模型。"""
    text = " ".join((first_message or "").split())
    if len(text) <= DEFAULT_TITLE_CHARS:
        return text
    return text[:DEFAULT_TITLE_CHARS].rstrip() + "…"


def default_project_title(relative: str, workspace_root: str) -> str:
    source = relative or workspace_root.rstrip("\\/")
    name = re.split(r"[\\/]", source)[-1] or source
    return name[:MAX_TITLE_CHARS]


def turn_settings(
    kind: ConversationKind | str,
    *,
    directory: str | None,
    environment_key: str | None,
    environment_online: bool,
    approval_policy: str,
    expert: bool = False,
) -> dict[str, Any]:
    """这一轮发给 Codex 的四样：执行环境、目录、沙箱策略、审批。每一轮都带全。

    同一条线上可以先聊天、再工作、再交给专家，所以不能指望上一轮留下的设置：
    这一轮是什么，就明明白白发什么。页面传来的设置一概不用（F-PROJ-03）。
    """
    kind = ConversationKind(kind)
    in_project = directory is not None and environment_key is not None
    if kind is ConversationKind.WORK or expert:
        if not in_project:
            raise ValueError("work and expert turns need a project")
        return {
            "environments": [{"environmentId": environment_key, "cwd": directory}],
            "cwd": directory,
            "sandboxPolicy": {
                "type": "workspaceWrite",
                "writableRoots": [],
                "networkAccess": False,
            },
            "approvalPolicy": approval_policy,
        }
    # 聊天：只读，越出只读的动作直接被拒，不问用户
    settings: dict[str, Any] = {
        "environments": [],
        "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
        "approvalPolicy": "never",
    }
    if in_project and environment_online:
        settings["environments"] = [
            {"environmentId": environment_key, "cwd": directory}
        ]
        settings["cwd"] = directory
    return settings
