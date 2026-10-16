"""作为运行方跑分身测试（SDD 0027 §3.4，契约 knowbot-test-run/v1，有人值守）。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261017_0020"
down_revision = "20261016_0019"
branch_labels = None
depends_on = None


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    # 一次测试：knowlab 给的请求原文与摘要（同一个 run_id 只认一份内容）、状态、花费
    op.create_table(
        "workbench_test_runs",
        sa.Column("run_id", sa.String(128), primary_key=True),
        sa.Column("caller", sa.String(32), nullable=False),
        sa.Column("owner_subject", sa.String(512), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("request", pg.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="accepted"),
        sa.Column("budget_limit", sa.Numeric(12, 2), nullable=False),
        sa.Column("spent", sa.Numeric(14, 6), nullable=False, server_default="0"),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_wait_seconds", sa.Integer(), nullable=False),
        sa.Column("waited_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        _ts("started_at"),
        _ts("finished_at"),
        _ts("cleanup_after"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'running', 'waiting', 'passed', 'failed',"
            " 'error', 'timeout', 'cancelled')",
            name="ck_workbench_test_runs_status",
        ),
    )
    op.create_index("ix_workbench_test_runs_status", "workbench_test_runs", ["status"])
    # 每条用例一行：按顺序一条一条跑，各自一个临时项目与委托
    op.create_table(
        "workbench_test_cases",
        sa.Column(
            "run_id",
            sa.String(128),
            sa.ForeignKey("workbench_test_runs.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("case_id", sa.String(64), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("project_id", pg.UUID(as_uuid=True)),
        sa.Column("session_id", pg.UUID(as_uuid=True)),
        sa.Column("task_id", pg.UUID(as_uuid=True)),
        sa.Column("result", pg.JSONB()),
        _ts("waiting_since"),
        sa.Column("waited_seconds", sa.Integer(), nullable=False, server_default="0"),
    )
    # knowlab 交来的答案：同一个问题只认一份（重复同内容回原状态，不同则 409）
    op.create_table(
        "workbench_test_answers",
        sa.Column(
            "run_id",
            sa.String(128),
            sa.ForeignKey("workbench_test_runs.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("question_id", sa.String(128), primary_key=True),
        sa.Column("answer", pg.JSONB(), nullable=False),
        sa.Column("answered_by", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # 委托来源多一种：测试中的草稿（快照同分身，不进钉表、不进用户的专家清单）
    op.drop_constraint("ck_workbench_tasks_pack_source", "workbench_tasks")
    op.create_check_constraint(
        "ck_workbench_tasks_pack_source",
        "workbench_tasks",
        "(pack_source = 'builtin' and pack_snapshot is null and pack_digest is null)"
        " or (pack_source in ('knowmart', 'knowlab-test') and pack_snapshot is not null"
        " and pack_digest is not null)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_workbench_tasks_pack_source", "workbench_tasks")
    op.create_check_constraint(
        "ck_workbench_tasks_pack_source",
        "workbench_tasks",
        "(pack_source = 'builtin' and pack_snapshot is null and pack_digest is null)"
        " or (pack_source = 'knowmart' and pack_snapshot is not null"
        " and pack_digest is not null)",
    )
    op.drop_table("workbench_test_answers")
    op.drop_table("workbench_test_cases")
    op.drop_index("ix_workbench_test_runs_status", table_name="workbench_test_runs")
    op.drop_table("workbench_test_runs")
