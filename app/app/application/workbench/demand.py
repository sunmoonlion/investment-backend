"""缺数据的需求（账 56）：各用户在对话里查不到数据的公司，汇总给管理端看。

需求从哪来：查数据的工具答复没有这个数据集、或专家的某一步交回说没入库时记下的
`data.missing` 事件（domain/workbench/missing_data）。这里只汇总，由我们决定采不采；
采集在 info 管理端发起。
"""

from __future__ import annotations

from typing import Any

from app.application.ports.workbench import WorkbenchStore

LIMIT = 200


class Demand:
    def __init__(self, store: WorkbenchStore):
        self.store = store

    async def summary(self, *, limit: int = LIMIT) -> list[dict[str, Any]]:
        """按证券代码：几次、几个人、最近一次、被查的数据集。最近的在前。"""
        return await self.store.summarize_missing_data(limit=max(1, min(limit, LIMIT)))
