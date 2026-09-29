"""「没有数据」（PRD/apps/investment.md 7.5、F-PROJ-07）。

什么时候算：查数据的工具答复「没有这个数据集」，而且被查的数据集名认得出证券代码；
或者专家的某一步交回说这家公司没有入库。认不出代码就不算：对话里照常显示工具的答复。
这里只有判断，不碰数据库；同一段对话同一个代码只记一次，由存储端保证。
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.workbench.packs import StepContract

EVENT = "data.missing"

# 数据集名的开头：市场加六位代码，例如 sh600519-financials
_NAMED = re.compile(r"^(sh|sz|bj)(\d{6})(?!\d)", re.IGNORECASE)
_CODE = re.compile(r"^\d{6}$")
# 知识服务答复没有这个数据集时的说法（knowledge-backend 的 dataset_catalog）
_UNKNOWN = "unknown dataset"


def security_of(dataset: Any) -> dict[str, str] | None:
    """从数据集名认出证券。认不出就是 None，不猜。"""
    if not isinstance(dataset, str):
        return None
    found = _NAMED.match(dataset.strip())
    if found is None:
        return None
    return {"security_code": found.group(2), "market": found.group(1).lower()}


def texts_of(item: dict[str, Any]) -> list[str]:
    said = []
    error = item.get("error")
    if isinstance(error, dict) and error.get("message"):
        said.append(str(error["message"]))
    result = item.get("result")
    if isinstance(result, dict):
        for part in result.get("content") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                said.append(part["text"])
    return said


def from_tool_call(item: Any) -> dict[str, Any] | None:
    """一次做完的工具调用，是不是在说「没有这家的数据」。"""
    if not isinstance(item, dict) or item.get("type") != "mcpToolCall":
        return None
    if item.get("status") != "failed":
        return None
    arguments = item.get("arguments")
    dataset = arguments.get("dataset") if isinstance(arguments, dict) else None
    security = security_of(dataset)
    if security is None:
        return None
    if not any(_UNKNOWN in text.lower() for text in texts_of(item)):
        return None
    return {**security, "dataset": dataset, "source": "tool"}


def from_step(step: StepContract, returned: Any) -> dict[str, Any] | None:
    """专家的这一步交回说没有入库。证券代码取交回物里写的；不是六位数字就不算。"""
    where = step.missing_data
    if where is None or not isinstance(returned, dict):
        return None
    flagged = returned.get(where.flag) is True
    absent = returned.get(where.dataset) in (None, "")
    if not (flagged or absent):
        return None
    code = returned.get(where.code)
    if not isinstance(code, str) or not _CODE.match(code.strip()):
        return None
    return {
        "security_code": code.strip(),
        "market": None,
        "dataset": None,
        "source": "expert",
    }
