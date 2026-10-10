"""Browser-approved agent pairing and short-lived install capabilities."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261009_0014"
down_revision = "20261007_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workbench_agent_pairings",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("device_secret_hash", sa.String(64), nullable=False),
        sa.Column("machine_name", sa.String(128), nullable=False),
        sa.Column("os", sa.String(64), nullable=False),
        sa.Column("agent_version", sa.String(64), nullable=False),
        sa.Column("codex_version", sa.String(64), nullable=False),
        sa.Column("source_ip", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("relay_user", sa.String(64), nullable=True),
        sa.Column("relay_url", sa.Text(), nullable=True),
        sa.Column("agent_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_ciphertext", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('pending','approved','delivered','denied','cancelled','expired')",
            name="ck_workbench_agent_pairing_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_workbench_agent_pairing_attempts"),
    )
    op.create_index(
        "ix_workbench_agent_pairings_pending_expiry",
        "workbench_agent_pairings",
        ["expires_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_workbench_agent_pairings_owner_created",
        "workbench_agent_pairings",
        ["owner_actor_id", "created_at"],
    )
    op.create_table(
        "workbench_agent_audit_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("pairing_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("metadata", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_workbench_agent_audit_owner_created",
        "workbench_agent_audit_events",
        ["owner_actor_id", "created_at"],
    )
    op.create_table(
        "workbench_agent_install_credentials",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("use_count between 0 and 5", name="ck_workbench_agent_install_uses"),
    )
    op.create_index(
        "ix_workbench_agent_install_expiry",
        "workbench_agent_install_credentials",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workbench_agent_install_expiry", table_name="workbench_agent_install_credentials")
    op.drop_table("workbench_agent_install_credentials")
    op.drop_index("ix_workbench_agent_audit_owner_created", table_name="workbench_agent_audit_events")
    op.drop_table("workbench_agent_audit_events")
    op.drop_index("ix_workbench_agent_pairings_owner_created", table_name="workbench_agent_pairings")
    op.drop_index("ix_workbench_agent_pairings_pending_expiry", table_name="workbench_agent_pairings")
    op.drop_table("workbench_agent_pairings")
