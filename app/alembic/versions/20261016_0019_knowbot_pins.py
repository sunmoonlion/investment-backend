"""请来的分身（SDD 0027 P4）：钉住的清单原文与摘要；委托记来源、摘要与包快照。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261016_0019"
down_revision = "20261012_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 用户从 knowmart 选定、工作台核验过的那一版。版本在 knowmart 不可改写，所以这里只插不改
    op.create_table(
        "workbench_knowbot_pins",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), nullable=False),
        # 跨应用的所有者是 Casdoor 用户 id，向 knowmart 核验时用它
        sa.Column("owner_subject", sa.String(512), nullable=False),
        sa.Column("knowbot_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.String(16), nullable=False),
        sa.Column("digest", sa.String(80), nullable=False),
        sa.Column("manifest", pg.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "owner_actor_id", "knowbot_id", "version", name="uq_workbench_knowbot_pin"
        ),
    )
    op.execute(
        """create function workbench_knowbot_pins_never_change() returns trigger
           language plpgsql as $$
           begin
             raise exception 'a pinned knowbot version never changes';
           end $$"""
    )
    op.execute(
        """create trigger workbench_knowbot_pins_frozen
           before update on workbench_knowbot_pins
           for each row execute function workbench_knowbot_pins_never_change()"""
    )
    # 委托从哪来：builtin = 内置专家包；knowmart = 请来的分身，快照就是这一版的专家包
    op.add_column(
        "workbench_tasks",
        sa.Column(
            "pack_source", sa.String(16), nullable=False, server_default="builtin"
        ),
    )
    op.add_column("workbench_tasks", sa.Column("pack_digest", sa.String(80)))
    op.add_column("workbench_tasks", sa.Column("pack_snapshot", pg.JSONB()))
    op.create_check_constraint(
        "ck_workbench_tasks_pack_source",
        "workbench_tasks",
        "(pack_source = 'builtin' and pack_snapshot is null and pack_digest is null)"
        " or (pack_source = 'knowmart' and pack_snapshot is not null"
        " and pack_digest is not null)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_workbench_tasks_pack_source", "workbench_tasks")
    op.drop_column("workbench_tasks", "pack_snapshot")
    op.drop_column("workbench_tasks", "pack_digest")
    op.drop_column("workbench_tasks", "pack_source")
    op.execute("drop trigger workbench_knowbot_pins_frozen on workbench_knowbot_pins")
    op.drop_table("workbench_knowbot_pins")
    op.execute("drop function workbench_knowbot_pins_never_change()")
