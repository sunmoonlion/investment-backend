"""长期记忆（0019 §四，账 85）：增、改、停用、删、清空；只看得见自己的。"""

from __future__ import annotations

from test_workbench_ledger_db import db as db  # noqa: F401
from test_workbench_routes import A, B
from test_workbench_routes import make_client as make_client  # noqa: F401


async def test_memories_are_mine_only_and_can_be_toggled(make_client, db):  # noqa: F811
    async with make_client(A) as http:
        empty = await http.get("/api/workbench/memories")
        assert empty.status_code == 200 and empty.json()["memories"] == []
        made = await http.post(
            "/api/workbench/memories", json={"text": "  我看银行股时用扣非净利润  "}
        )
        assert made.status_code == 201
        memory = made.json()["memory"]
        assert (
            memory["text"] == "我看银行股时用扣非净利润" and memory["enabled"] is True
        )
        assert memory["kind"] == "fact"
        off = await http.patch(
            f"/api/workbench/memories/{memory['id']}", json={"enabled": False}
        )
        assert off.status_code == 200 and off.json()["memory"]["enabled"] is False
        renamed = await http.patch(
            f"/api/workbench/memories/{memory['id']}",
            json={"text": "看扣非", "enabled": True},
        )
        assert renamed.json()["memory"] == {
            **memory,
            "text": "看扣非",
            "updated_at": renamed.json()["memory"]["updated_at"],
        }
        listed = (await http.get("/api/workbench/memories")).json()["memories"]
        assert [m["text"] for m in listed] == ["看扣非"]
        bad = await http.post("/api/workbench/memories", json={"text": ""})
        assert bad.status_code == 422
    async with make_client(B) as other:
        assert (await other.get("/api/workbench/memories")).json()["memories"] == []
        stolen = await other.delete(f"/api/workbench/memories/{memory['id']}")
        assert stolen.status_code == 404
    async with make_client(A) as http:
        gone = await http.delete(f"/api/workbench/memories/{memory['id']}")
        assert gone.status_code == 204
        await http.post("/api/workbench/memories", json={"text": "一"})
        await http.post("/api/workbench/memories", json={"text": "二"})
        cleared = await http.delete("/api/workbench/memories")
        assert cleared.status_code == 200 and cleared.json()["removed"] == 2
        assert (await http.get("/api/workbench/memories")).json()["memories"] == []
