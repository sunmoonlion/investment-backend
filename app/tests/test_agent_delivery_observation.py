"""The read-only observer must use Agent receipts/leases, not the empty registry."""

import pytest
from sqlalchemy.exc import DBAPIError
from test_agent_reliability_db import db as db
from test_agent_reliability_db import phase0, scalar, sql

from app.infrastructure.agent.delivery import CONSUMER, AgentDelivery
from app.infrastructure.messaging.delivery_observation import collect_delivery_snapshot
from app.infrastructure.messaging.delivery_observers import get_delivery_observers


async def observe(db):
    policies = get_delivery_observers(db)
    assert isinstance(policies["agent"], AgentDelivery)
    assert policies["agent"].topics == AgentDelivery(db).topics
    snapshot = await collect_delivery_snapshot(db, policies)
    assert snapshot["unregistered_messages"] == 0
    return {row["topic"]: row for row in snapshot["topics"]}


async def test_agent_execution_uses_real_session_lease_and_exact_consumer(db):
    _, _, command = await phase0(db)
    await sql(
        db,
        """
        UPDATE outbox_message SET status='published',
            published_at=clock_timestamp()-interval '5 minutes' WHERE id=:id
    """,
        id=command,
    )
    delivery = AgentDelivery(db)
    lease, _ = await delivery.claim_execution(command)
    row = (await observe(db))["agent.execution"]
    assert row["incomplete_messages"] == row["active_execution_messages"] == 1
    assert row["reconcile_candidates"] == 0
    # Public execution storage is unused; falling back to it would falsely stall.
    assert await scalar(db, "SELECT count(*) FROM outbox_execution") == 0
    await sql(
        db,
        """
        INSERT INTO inbox_message(consumer,message_id) VALUES ('agent.execution',:id)
    """,
        id=command,
    )
    assert (await observe(db))["agent.execution"]["incomplete_messages"] == 1
    await delivery.release(lease)
    assert (await observe(db))["agent.execution"]["reconcile_candidates"] == 1
    await sql(
        db,
        """
        INSERT INTO inbox_message(consumer,message_id) VALUES (:consumer,:id)
    """,
        id=command,
        consumer=CONSUMER,
    )
    row = (await observe(db))["agent.execution"]
    assert row["incomplete_messages"] == row["reconcile_candidates"] == 0


async def test_notification_publication_is_not_misreported_as_missing_agent_ack(db):
    await phase0(db)
    before = (await observe(db))["agent.notification"]
    assert before["messages"] > 0
    assert before["incomplete_messages"] == before["messages"]
    await sql(
        db,
        """
        UPDATE outbox_message SET status='published',
            published_at=clock_timestamp()-interval '5 minutes'
        WHERE topic='agent.notification'
    """,
    )
    row = (await observe(db))["agent.notification"]
    assert not row["receipt_required"]
    assert row["incomplete_messages"] == row["awaiting_receipt_messages"] == 0
    assert row["reconcile_candidates"] == 0
    assert await scalar(db, "SELECT count(*) FROM inbox_message") == 0


async def test_missing_domain_lease_store_fails_instead_of_falling_back(db):
    await phase0(db)
    await sql(db, "ALTER TABLE agent_execution_leases RENAME TO missing_agent_leases")
    with pytest.raises(DBAPIError):
        await observe(db)
