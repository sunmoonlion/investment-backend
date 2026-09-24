"""工作台（0001-workbench）：Session 与方向盘、Task/Attempt 两层、Interaction、Artifact、预算账、幂等账、事件流。

设计真源：k8s/sunmoonai/docs/dev-investment-agent/tree-build/SDD/architecture/{state-machine,task-contract,wheel,persistence}.md。
新表一律以 workbench_ 为前缀，与旧 agent_*（Pilot 链）并存；旧表按 expand → contract 退役，不在本迁移里删。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20260924_0008"
down_revision = "20260911_0007"
branch_labels = None
depends_on = None


def _ts(name: str, **kw):
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
        **kw,
    )


def upgrade():
    op.create_table(
        "workbench_environments",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("agent_version", sa.String(64), nullable=True),
        sa.Column("codex_version", sa.String(64), nullable=True),
        sa.Column(
            "roots", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column(
            "ceiling", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="registered"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index(
        "ix_workbench_environments_owner", "workbench_environments", ["owner_actor_id"]
    )

    op.create_table(
        "workbench_sandboxes",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("app_server_url", sa.Text(), nullable=False),
        sa.Column("token_ref", sa.String(256), nullable=False),
        sa.Column("codex_version", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="registered"),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index(
        "ix_workbench_sandboxes_owner", "workbench_sandboxes", ["owner_actor_id"]
    )

    op.create_table(
        "workbench_sessions",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "environment_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workbench_environments.id"),
            nullable=False,
        ),
        sa.Column(
            "sandbox_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workbench_sandboxes.id"),
            nullable=False,
        ),
        sa.Column("project_root", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=True),
        sa.Column("wheel", sa.String(16), nullable=False, server_default="user"),
        sa.Column("state_version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("active_task_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "thread_settings",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _ts("created_at"),
        _ts("last_active_at"),
        sa.CheckConstraint(
            "wheel in ('user','advisor')", name="ck_workbench_sessions_wheel"
        ),
    )
    op.create_index(
        "ix_workbench_sessions_owner", "workbench_sessions", ["owner_actor_id"]
    )

    op.create_table(
        "workbench_session_events",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column(
            "session_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workbench_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cursor", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column(
            "payload", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("task_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        _ts("created_at"),
        sa.UniqueConstraint(
            "session_id", "cursor", name="uq_workbench_session_events_cursor"
        ),
    )

    op.create_table(
        "workbench_tasks",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column(
            "session_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workbench_sessions.id"),
            nullable=False,
        ),
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant", sa.String(64), nullable=False, server_default="default"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("profile_id", sa.String(128), nullable=False),
        sa.Column("profile_version", sa.String(64), nullable=False),
        sa.Column("expert_pack_version", sa.String(64), nullable=True),
        sa.Column("original_input", pg.JSONB(), nullable=False),
        sa.Column("normalized_goal", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.String(128), nullable=True),
        sa.Column("environment_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("project_root", sa.Text(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("state_version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("workflow_version", sa.String(64), nullable=True),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "acceptance_contract",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "execution_policy",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("budget", pg.JSONB(), nullable=False),
        sa.Column("active_attempt_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("terminal_result_ref", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("waiting_reason", sa.String(32), nullable=True),
        sa.Column("active_interaction_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("rejection", pg.JSONB(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_by", pg.UUID(as_uuid=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint(
            "tenant",
            "owner_actor_id",
            "profile_id",
            "idempotency_key",
            name="uq_workbench_tasks_idempotency",
        ),
    )
    op.create_index("ix_workbench_tasks_session", "workbench_tasks", ["session_id"])
    op.create_index("ix_workbench_tasks_state", "workbench_tasks", ["state"])

    op.create_table(
        "workbench_attempts",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column(
            "task_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workbench_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=True),
        sa.Column("environment_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(16), nullable=False, server_default="execute"),
        sa.Column("arm", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_id", sa.String(128), nullable=True),
        sa.Column("step_version", sa.String(64), nullable=True),
        sa.Column(
            "input_artifact_versions",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "turn_ids",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("codex_version", sa.String(64), nullable=True),
        sa.Column("agent_version", sa.String(64), nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("model_provider", sa.String(128), nullable=True),
        sa.Column(
            "budget_allocated",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "budget_consumed",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column(
            "output_artifacts",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "refs", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.CheckConstraint(
            "role in ('execute','acceptance','competitor','judge')",
            name="ck_workbench_attempts_role",
        ),
    )
    op.create_index("ix_workbench_attempts_task", "workbench_attempts", ["task_id"])

    op.create_table(
        "workbench_interactions",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column(
            "session_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workbench_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workbench_tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("attempt_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("audience", sa.String(32), nullable=False, server_default="owner"),
        sa.Column("prompt", pg.JSONB(), nullable=False),
        sa.Column("subject_digest", sa.String(64), nullable=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("target_state_version", sa.BigInteger(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("response", pg.JSONB(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_by", pg.UUID(as_uuid=True), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("token_hash", name="uq_workbench_interactions_token"),
        sa.CheckConstraint(
            "status in ('pending','consumed','expired','cancelled')",
            name="ck_workbench_interactions_status",
        ),
    )
    op.create_index(
        "ix_workbench_interactions_session_status",
        "workbench_interactions",
        ["session_id", "status"],
    )

    op.create_table(
        "workbench_artifacts",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column(
            "task_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workbench_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content", pg.JSONB(), nullable=True),
        sa.Column("content_ref", sa.Text(), nullable=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("workspace_path", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint(
            "task_id", "name", "version", name="uq_workbench_artifacts_version"
        ),
    )

    op.create_table(
        "workbench_budget_ledger",
        # UUID like every other workbench table: the KIND identity policy reviews
        # only plain tables (no sequences) and inserts must not need a sequence grant.
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column(
            "task_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("workbench_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("entry", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(18, 6), nullable=False),
        sa.Column(
            "tokens", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("actor_id", pg.UUID(as_uuid=True), nullable=True),
        _ts("created_at"),
        sa.CheckConstraint(
            "entry in ('reserve','consume','release','topup','settle')",
            name="ck_workbench_budget_entry",
        ),
    )
    op.create_index("ix_workbench_budget_task", "workbench_budget_ledger", ["task_id"])

    op.create_table(
        "workbench_approval_log",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("session_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("method", sa.String(128), nullable=False),
        sa.Column("summary", pg.JSONB(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("interaction_id", pg.UUID(as_uuid=True), nullable=True),
        _ts("created_at"),
    )
    op.create_index(
        "ix_workbench_approval_session", "workbench_approval_log", ["session_id"]
    )

    # 网页接口 → runner 的命令队列（同一事务里与事件一起落库；runner 用 skip locked 认领）
    op.create_table(
        "workbench_commands",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("session_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("sandbox_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "payload",
            pg.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.CheckConstraint(
            "status in ('pending','claimed','done','failed')",
            name="ck_workbench_commands_status",
        ),
    )
    op.create_index(
        "ix_workbench_commands_pending",
        "workbench_commands",
        ["status", "created_at"],
    )


def downgrade():
    for name in (
        "workbench_commands",
        "workbench_approval_log",
        "workbench_budget_ledger",
        "workbench_artifacts",
        "workbench_interactions",
        "workbench_attempts",
        "workbench_tasks",
        "workbench_session_events",
        "workbench_sessions",
        "workbench_sandboxes",
        "workbench_environments",
    ):
        op.drop_table(name)
