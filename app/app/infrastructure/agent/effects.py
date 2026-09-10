from __future__ import annotations

import json

from sqlalchemy import text

from app.infrastructure.agent.transactions import AgentTransactions, atomic


class EffectRepository(AgentTransactions):
    @atomic
    async def prepare(self, *, key: str, run_id: str, intent: dict) -> dict:
        await self.assert_lease()
        assert self.lease is not None
        target = await self.session.execute(
            text("select session_id from agent_runs where id=:id"), {"id": run_id}
        )
        if target.scalar_one_or_none() != self.lease.session_id:
            raise ValueError("side effect does not belong to the leased session")
        await self.session.execute(
            text("""
            INSERT INTO tool_side_effects(tool_call_id, run_id, status, result, intent, execution_epoch)
            VALUES (:key,:run,'pending','{}'::jsonb,CAST(:intent AS jsonb),:epoch)
            ON CONFLICT(tool_call_id) DO NOTHING
        """),
            {
                "key": key,
                "run": run_id,
                "intent": json.dumps(intent),
                "epoch": self.lease.epoch,
            },
        )
        row = await self.session.execute(
            text("select * from tool_side_effects where tool_call_id=:key for update"),
            {"key": key},
        )
        result = dict(row.mappings().one())
        if str(result["run_id"]) != run_id or result["intent"] != intent:
            raise ValueError("side-effect key reused for another operation")
        return result

    @atomic
    async def begin(self, key: str) -> bool:
        await self.assert_lease()
        assert self.lease is not None
        result = await self.session.execute(
            text("""
            UPDATE tool_side_effects SET status='executing', execution_epoch=:epoch,
                updated_at=clock_timestamp() WHERE tool_call_id=:key AND status='pending'
              AND run_id IN (SELECT id FROM agent_runs WHERE session_id=:session_id)
            RETURNING tool_call_id
        """),
            {
                "key": key,
                "epoch": self.lease.epoch,
                "session_id": self.lease.session_id,
            },
        )
        return result.scalar_one_or_none() is not None

    @atomic
    async def settle(
        self, key: str, *, status: str, receipt: dict | None = None
    ) -> None:
        await self.assert_lease()
        assert self.lease is not None
        if status not in {"completed", "unknown", "failed"}:
            raise ValueError("invalid side-effect outcome")
        result = await self.session.execute(
            text("""
            UPDATE tool_side_effects SET status=:status, receipt=CAST(:receipt AS jsonb),
                result=CAST(:receipt AS jsonb), updated_at=clock_timestamp()
            WHERE tool_call_id=:key AND status IN ('executing','unknown')
              AND run_id IN (SELECT id FROM agent_runs WHERE session_id=:session_id) RETURNING tool_call_id
        """),
            {
                "key": key,
                "status": status,
                "receipt": json.dumps(receipt or {}),
                "session_id": self.lease.session_id,
            },
        )
        if result.scalar_one_or_none() is None:
            raise ValueError("side-effect outcome no longer writable")
