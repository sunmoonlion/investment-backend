"""知识库（SDD 0011）：用户在我们这里的资料，每一份在服务端有一份只属于他的副本。

第一期里面有两种东西，都是已经存在的：专家交回的底稿、各步交回的东西（交回物）。
这里是纯函数：资料的标识怎么编、一条清单长什么样、引用怎么写。不读库。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from app.domain.workbench.records import clip, one_line

KIND_DOSSIER = "dossier"
KIND_DELIVERABLE = "deliverable"
KINDS = (KIND_DOSSIER, KIND_DELIVERABLE)
TITLE_CHARS = 120
PAGE_CHARS = 30000
_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def dossier_id(task_id: str) -> str:
    return f"{KIND_DOSSIER}:{task_id}"


def deliverable_id(task_id: str, name: str) -> str:
    return f"{KIND_DELIVERABLE}:{task_id}:{name}"


def parse_item_id(value: Any) -> tuple[str, str, str | None] | None:
    """`dossier:<委托>` 或 `deliverable:<委托>:<名字>`；不是这个样子就是 None。"""
    if not isinstance(value, str):
        return None
    parts = value.split(":", 2)
    if len(parts) < 2 or parts[0] not in KINDS:
        return None
    try:
        task_id = str(uuid.UUID(parts[1]))
    except ValueError:
        return None
    if parts[0] == KIND_DOSSIER:
        return (KIND_DOSSIER, task_id, None) if len(parts) == 2 else None
    if len(parts) != 3 or not _NAME.match(parts[2]):
        return None
    return (KIND_DELIVERABLE, task_id, parts[2])


def default_dossier_title(expert: str, question: str) -> str:
    asked = one_line(question, TITLE_CHARS)
    return f"{expert}：{asked}" if asked else expert or "底稿"


def default_deliverable_title(step_title: str, name: str, expert: str) -> str:
    head = step_title or name
    return f"{head}（{expert}）" if expert else head


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def as_text(content: Any) -> str:
    """交回物的内容给人和模型看的样子：本来是文本就原样，不是就排好的 JSON。"""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, indent=2, default=str)


def page_of(text: str, page: int, size: int = PAGE_CHARS) -> dict[str, Any]:
    pages = max(1, -(-len(text) // size))
    page = min(max(1, int(page or 1)), pages)
    start = (page - 1) * size
    return {"text": text[start : start + size], "page": page, "pages": pages}


def citation(item_id: str, version: int, sha256: str) -> dict[str, Any]:
    """Codex 引用知识库资料时带的三元组，和引用公共数据的 {dataset, data_version} 一样可核对。"""
    return {"library_item_id": item_id, "version": version, "sha256": sha256}


__all__ = [
    "KIND_DELIVERABLE",
    "KIND_DOSSIER",
    "KINDS",
    "as_text",
    "citation",
    "clip",
    "default_deliverable_title",
    "default_dossier_title",
    "deliverable_id",
    "dossier_id",
    "page_of",
    "parse_item_id",
    "sha256_of",
]
