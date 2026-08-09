from __future__ import annotations

from typing import Any, Protocol


class ToolSideEffectStore(Protocol):
    async def record_side_effect_once(
        self,
        *,
        tool_call_id: str,
        run_id: str,
        result: dict[str, Any] | None = None,
    ) -> bool: ...


class ToolSideEffectService:
    def __init__(self, store: ToolSideEffectStore):
        self.store = store

    async def record_once(
        self,
        *,
        tool_call_id: str,
        run_id: str,
        result: dict[str, Any] | None = None,
    ) -> bool:
        return await self.store.record_side_effect_once(
            tool_call_id=tool_call_id,
            run_id=run_id,
            result=result,
        )
