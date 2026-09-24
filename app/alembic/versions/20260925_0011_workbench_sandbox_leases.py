"""runner 多实例分片（F-LEDGER-02 的另一半）：每个沙箱同一时刻只由一个 runner 持有（租约，过期可接管）。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20260925_0011"
down_revision = "20260925_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workbench_sandbox_leases",
        sa.Column("sandbox_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("runner_id", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_workbench_sandbox_leases_runner", "workbench_sandbox_leases", ["runner_id"]
    )


def downgrade():
    op.drop_table("workbench_sandbox_leases")
