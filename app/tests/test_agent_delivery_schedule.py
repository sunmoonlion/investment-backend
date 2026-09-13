"""The session-lease overlay must enforce the same timer as generic consumers."""

from datetime import timedelta

import pytest
from test_agent_reliability_db import db as db
from test_agent_reliability_db import phase0, scalar, sql

from app.application.services.durable_tasks import enqueue_task
from app.infrastructure.agent.delivery import AgentDelivery


@pytest.mark.parametrize("seconds", [-1, 3600])
async def test_agent_overlay_respects_original_timer_even_after_replay(db, seconds):
    run_id, _, original = await phase0(db)
    payload = await scalar(
        db, "SELECT payload FROM outbox_message WHERE id=:id", id=original
    )
    when = await scalar(db, "SELECT clock_timestamp()") + timedelta(seconds=seconds)
    async with db() as s, s.begin():
        command = await enqueue_task(
            s,
            topic="agent.execution",
            key=run_id,
            payload=payload,
            deduplication_key="scheduled-execution",
            not_before=when,
        )
    delivery = AgentDelivery(db)
    if seconds > 0:
        assert await delivery.claim_execution(command) is None
        assert await scalar(db, "SELECT count(*) FROM agent_execution_leases") == 0
        await sql(
            db,
            "INSERT INTO outbox_dead_letter(message_id,error_code) "
            "VALUES (:id,'injected')",
            id=command,
        )
        await delivery.replay(command)
        assert await delivery.claim_execution(command) is None
        assert await scalar(db, "SELECT count(*) FROM agent_execution_leases") == 0
    else:
        claimed = await delivery.claim_execution(command)
        assert claimed is not None
        assert claimed[1] == payload
        await delivery.release(claimed[0])
