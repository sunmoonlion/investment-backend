"""One shared dead-letter journal; preserve the Agent execution discipline."""

import sqlalchemy as sa

from alembic import op
from app.infrastructure.messaging.delivery_schema import downgrade as shared_downgrade
from app.infrastructure.messaging.delivery_schema import upgrade as shared_upgrade

revision = "20260911_0007"
down_revision = "20260910_0006"
branch_labels = None
depends_on = None


def upgrade():
    shared_upgrade()
    op.execute("""
        INSERT INTO outbox_dead_letter(message_id,error_code,failed_at,replayed_at)
        SELECT message_id,error_code,failed_at,replayed_at FROM agent_delivery_failures
    """)
    op.rename_table("agent_delivery_failures", "agent_delivery_failures_legacy_0006")
    # Keep the rollback snapshot without leaving a second writable authority.
    # The old table name is absent, so stale workers fail closed after cutover.
    op.execute("""
        CREATE FUNCTION agent_delivery_archive_readonly() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Agent delivery archive is read-only; use outbox_dead_letter';
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER agent_delivery_archive_readonly
        BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
        ON agent_delivery_failures_legacy_0006
        FOR EACH STATEMENT EXECUTE FUNCTION agent_delivery_archive_readonly()
    """)


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("""
        SELECT EXISTS(SELECT 1 FROM outbox_execution WHERE expires_at>clock_timestamp())
            OR EXISTS(SELECT 1 FROM agent_execution_leases WHERE expires_at>clock_timestamp())
    """)).scalar_one():
        raise RuntimeError("stop and drain active executions before downgrade")
    if connection.execute(sa.text("""
        SELECT EXISTS(
            SELECT 1 FROM outbox_message m
            WHERE m.topic NOT IN ('agent.execution','agent.notification') AND (
                EXISTS(SELECT 1 FROM outbox_dead_letter d WHERE d.message_id=m.id)
                OR NOT EXISTS(SELECT 1 FROM inbox_message i
                    WHERE i.message_id=m.id AND i.consumer=m.topic)))
    """)).scalar_one():
        raise RuntimeError("non-Agent delivery state requires a verified backup restore")
    op.execute("""
        DROP TRIGGER agent_delivery_archive_readonly
        ON agent_delivery_failures_legacy_0006
    """)
    op.execute("DROP FUNCTION agent_delivery_archive_readonly()")
    # Carry forward both old and newly created/replayed failures, not just the
    # pre-upgrade snapshot. Inbox and domain tables are never cleared.
    op.execute("""
        INSERT INTO agent_delivery_failures_legacy_0006
            (message_id,error_code,failed_at,replayed_at)
        SELECT d.message_id,d.error_code,d.failed_at,d.replayed_at
        FROM outbox_dead_letter d JOIN outbox_message m ON m.id=d.message_id
        WHERE m.topic IN ('agent.execution','agent.notification')
        ON CONFLICT(message_id) DO UPDATE SET error_code=EXCLUDED.error_code,
            failed_at=EXCLUDED.failed_at,replayed_at=EXCLUDED.replayed_at
    """)
    shared_downgrade()
    op.rename_table("agent_delivery_failures_legacy_0006", "agent_delivery_failures")
