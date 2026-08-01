"""isolated P0-008C agent pilot request/control journal

Revision ID: 20260729_0003
Revises: 20260712_0002
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260729_0003"
down_revision = "20260712_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_pilot_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("user_input", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "owner_actor_id",
            "idempotency_key",
            name="uq_agent_pilot_owner_idempotency",
        ),
        sa.UniqueConstraint("run_id", name="uq_agent_pilot_request_run"),
    )
    op.create_table(
        "agent_pilot_controls",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("resume_action_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "resume_idempotency_key", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.id"], ondelete="CASCADE"
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_pilot_controls")
    op.drop_table("agent_pilot_requests")
