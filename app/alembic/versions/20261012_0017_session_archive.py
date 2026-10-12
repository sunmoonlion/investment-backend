"""对话可从工作台移除：workbench_sessions 加 archived_at（0016 侧边栏删除钮，账 90）。

删的是工作台里的记录，不是用户电脑上的东西；历史、审批日志都留着，只是不再列出。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261012_0017"
down_revision = "20261012_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workbench_sessions",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_workbench_sessions_owner_live",
        "workbench_sessions",
        ["owner_actor_id", "last_active_at"],
        postgresql_where=sa.text("archived_at is null"),
    )


def downgrade() -> None:
    op.drop_index("ix_workbench_sessions_owner_live", table_name="workbench_sessions")
    op.drop_column("workbench_sessions", "archived_at")
