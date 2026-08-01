from __future__ import annotations

from typing import Any

import pytest

from app.application.agent.side_effect_service import ToolSideEffectService


class FakeSideEffectStore:
    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.records: list[dict[str, Any]] = []

    async def record_side_effect_once(
        self,
        *,
        tool_call_id: str,
        run_id: str,
        result: dict[str, Any] | None = None,
    ) -> bool:
        if tool_call_id in self.seen:
            return False
        self.seen.add(tool_call_id)
        self.records.append(
            {"tool_call_id": tool_call_id, "run_id": run_id, "result": result or {}}
        )
        return True


@pytest.mark.asyncio
async def test_tool_side_effect_service_records_once_by_tool_call_id() -> None:
    store = FakeSideEffectStore()
    service = ToolSideEffectService(store)

    first = await service.record_once(
        tool_call_id="tool-1",
        run_id="run-1",
        result={"ok": True},
    )
    replay = await service.record_once(
        tool_call_id="tool-1",
        run_id="run-1",
        result={"ok": True},
    )

    assert first is True
    assert replay is False
    assert store.records == [
        {"tool_call_id": "tool-1", "run_id": "run-1", "result": {"ok": True}}
    ]
