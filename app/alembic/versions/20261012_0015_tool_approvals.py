"""工具级审批重做（账 77、79）：审批日志加方向盘与步骤，任务内授权表；对话加第三种 expert（账 82）。"""

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
    # 第三种对话：专家（0017）。专家和工作一样必须属于一个项目
    op.drop_constraint("ck_workbench_sessions_kind", "workbench_sessions")
    op.create_check_constraint(
        "ck_workbench_sessions_kind",
        "workbench_sessions",
        "kind in ('chat','work','expert')",
    )
    op.drop_constraint("ck_workbench_sessions_work_in_project", "workbench_sessions")
    op.create_check_constraint(
        "ck_workbench_sessions_work_in_project",
        "workbench_sessions",
        "kind = 'chat' OR project_id IS NOT NULL",
    )
    # 专家团：同一项目同一时刻只跑一位，排在后面的（RECEIVED 且记着 queued_behind）不占这个名额
    op.drop_index("uq_workbench_tasks_project_active", table_name="workbench_tasks")
    op.create_index(
        "uq_workbench_tasks_project_active",
        "workbench_tasks",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "project_id IS NOT NULL AND state NOT IN ('SUCCEEDED','REJECTED','FAILED','CANCELLED') "
            "AND NOT (state = 'RECEIVED' AND coalesce(original_input->'client_context'->>'queued_behind', '') <> '')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_workbench_tasks_project_active", table_name="workbench_tasks")
    op.create_index(
        "uq_workbench_tasks_project_active",
        "workbench_tasks",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text(
            "project_id IS NOT NULL AND state NOT IN ('SUCCEEDED','REJECTED','FAILED','CANCELLED')"
        ),
    )
    op.drop_constraint("ck_workbench_sessions_work_in_project", "workbench_sessions")
    op.create_check_constraint(
        "ck_workbench_sessions_work_in_project",
        "workbench_sessions",
        "kind <> 'work' OR project_id IS NOT NULL",
    )
    op.drop_constraint("ck_workbench_sessions_kind", "workbench_sessions")
    op.create_check_constraint(
        "ck_workbench_sessions_kind", "workbench_sessions", "kind in ('chat','work')"
    )
    op.drop_index(
        "ix_workbench_tool_grants_session", table_name="workbench_tool_grants"
    )
    op.drop_table("workbench_tool_grants")
    op.drop_index("ix_workbench_approval_task", table_name="workbench_approval_log")
    op.drop_column("workbench_approval_log", "step_id")
    op.drop_column("workbench_approval_log", "wheel")
