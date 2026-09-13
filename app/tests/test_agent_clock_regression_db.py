"""Domain lease overlay must preserve explicit release/cancellation semantics."""

import pytest
from test_agent_reliability_db import db as db
from test_agent_reliability_db import phase0, pilot, scalar
from test_delivery_clock_regression_db import shifted_database_clock

from app.infrastructure.agent.delivery import AgentDelivery
from app.infrastructure.agent.pilot_repository import PilotRepository
from app.infrastructure.agent.transactions import AgentTransactions, LeaseLost


@pytest.mark.parametrize("operation", ["claim", "renew", "guard"])
async def test_agent_released_lease_cannot_revive_on_clock_regression(db, operation):
    _, _, command = await phase0(db)
    delivery = AgentDelivery(db)
    lease, _ = await delivery.claim_execution(command)
    await delivery.release(lease)
    with shifted_database_clock(db):
        if operation == "claim":
            replacement = await delivery.claim_execution(command)
            assert replacement is not None
            assert replacement[0].epoch == lease.epoch + 1
            await delivery.release(lease)
            await delivery.renew(replacement[0])
        elif operation == "renew":
            with pytest.raises(LeaseLost):
                await delivery.renew(lease)
        else:
            async with db() as session:
                with pytest.raises(LeaseLost):
                    await AgentTransactions(session, lease=lease).assert_lease()


async def test_cancelled_agent_lease_is_not_active_after_clock_regression(db):
    run, actor = await pilot(db)
    command = await scalar(
        db, "SELECT id FROM outbox_message WHERE topic='agent.execution'"
    )
    delivery = AgentDelivery(db)
    lease, _ = await delivery.claim_execution(command)
    async with db() as session:
        await PilotRepository(session).request_cancel(
            run_id=run["id"], owner_actor_id=actor
        )
    with shifted_database_clock(db):
        with pytest.raises(LeaseLost):
            await delivery.renew(lease)
        assert (
            await scalar(
                db,
                "SELECT count(*) FROM agent_execution_leases WHERE expires_at>clock_timestamp()",
            )
            == 0
        )
        # Delivery can recover the original command to acknowledge cancellation;
        # acquiring a lease must not mutate the cancelled domain status.
        replacement = await delivery.claim_execution(command)
        assert replacement is not None and replacement[0].epoch == lease.epoch + 2
        await delivery.release(lease)
        await delivery.renew(replacement[0])
    assert await scalar(db, "SELECT status FROM agent_runs") == "cancelled"
