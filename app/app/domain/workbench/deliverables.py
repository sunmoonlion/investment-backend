"""专家步骤的成稿文件（0015 §3.3，账 80）。

成稿写进项目目录，是给用户的唯一一份；工作台只留证据。现在能看到的「写了没有」只有一条路：
这一步的 turn 里 Codex 报过的 `fileChange` 项（补丁改的文件）。命令行写的看不见，所以缺了不算
验收不过，只记一条事件、不填 `workspace_path`；硬检查等本地代理开出文件通道（账 87）之后用
`fs/getMetadata` 做。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# 各专家包成稿步骤共用的写法要求（原 PRD 七的底稿区块规则改写成对文件的要求）
DELIVERABLE_RULES = (
    "Write the deliverable as a Markdown file for the user, in the user's language. "
    "Sections, in this order: 回答 (the answer in plain words); 数据 (every number as a "
    "table with its source, period and 口径, and a footnote mark pointing to the source); "
    "专家验过什么 (what you checked and how); 局限 (what you could not do or verify); "
    "出处 (one line per source: dataset or file path and the date). "
    "Never give a rating, a target price or a buy/sell recommendation. "
    "Do not paste raw JSON into the file."
)

HANDBACK_FILE = "交回.md"
HANDBACK_RULES = (
    f"Also write `{HANDBACK_FILE}` at the project root (overwrite if it exists), for the user "
    "to keep: 做了什么 (each step in one line); 验过什么 (what was checked); 未知 (what "
    "stays unknown); 改了哪些文件 (every file you created or changed in this project, one "
    "per line); 怎么恢复 (how to undo the changes if the user wants to)."
)


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip("/").lower()


def deliverable_seen(paths: Iterable[str], output_file: str) -> bool:
    """这一轮 Codex 报过的 fileChange 路径里有没有 `output_file`。路径按结尾比：报的是绝对路径。"""
    wanted = _norm(output_file)
    if not wanted:
        return False
    for raw in paths:
        path = _norm(str(raw or ""))
        if path == wanted or path.endswith("/" + wanted):
            return True
    return False


def changed_paths(item: Any) -> list[str]:
    """一个 `fileChange` 条目里改到的路径；别的条目给空。"""
    if not isinstance(item, dict) or item.get("type") != "fileChange":
        return []
    return [
        str(change.get("path"))
        for change in item.get("changes") or []
        if isinstance(change, dict) and change.get("path")
    ]
