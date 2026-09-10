"""Agent delivery journal, execution fencing and side-effect receipts.

Existing run/event tables remain authoritative. New delivery rows use the
existing transactional outbox; this revision adds only Agent-owned extensions.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from alembic import op

revision = "20260910_0006"
down_revision = "20260811_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("execution_state", pg.JSONB(), nullable=False,
                                        server_default=sa.text("'{}'::jsonb")))
    op.create_table("agent_execution_leases",
        sa.Column("session_id", pg.UUID(), sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("command_id", pg.UUID(), sa.ForeignKey("outbox_message.id"), nullable=False),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table("agent_delivery_failures",
        sa.Column("message_id", pg.UUID(), sa.ForeignKey("outbox_message.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("error_code", sa.String(256), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("replayed_at", sa.DateTime(timezone=True)),
    )
    op.add_column("tool_side_effects", sa.Column("intent", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("tool_side_effects", sa.Column("execution_epoch", sa.BigInteger()))
    op.add_column("tool_side_effects", sa.Column("receipt", pg.JSONB()))
    op.create_check_constraint("ck_tool_side_effect_status", "tool_side_effects",
                               "status in ('pending', 'executing', 'unknown', 'completed', 'failed')")


def downgrade():
    op.drop_constraint("ck_tool_side_effect_status", "tool_side_effects")
    op.drop_column("tool_side_effects", "receipt")
    op.drop_column("tool_side_effects", "execution_epoch")
    op.drop_column("tool_side_effects", "intent")
    op.drop_table("agent_delivery_failures")
    op.drop_table("agent_execution_leases")
    op.drop_column("agent_runs", "execution_state")
