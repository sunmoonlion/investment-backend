"""工具级审批重做（账 77、79）：审批日志加方向盘与步骤，任务内授权表。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261012_0015"
down_revision = "20261009_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 每一次决定都要能回答：哪个方向盘、哪一步（approval.md「看得见」）
    op.add_column(
        "workbench_approval_log",
        sa.Column("wheel", sa.String(16), nullable=True),
    )
    op.add_column(
        "workbench_approval_log",
        sa.Column("step_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_workbench_approval_task", "workbench_approval_log", ["task_id"])
    # 「这个任务里都允许」：工作台自己记的授权，范围 = Task（自驾 = 到下一次交接为止）
    op.create_table(
        "workbench_tool_grants",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("session_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("command_prefix", pg.JSONB(), nullable=True),
        sa.Column("granted_by", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_workbench_tool_grants_session", "workbench_tool_grants", ["session_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workbench_tool_grants_session", table_name="workbench_tool_grants"
    )
    op.drop_table("workbench_tool_grants")
    op.drop_index("ix_workbench_approval_task", table_name="workbench_approval_log")
    op.drop_column("workbench_approval_log", "step_id")
    op.drop_column("workbench_approval_log", "wheel")
