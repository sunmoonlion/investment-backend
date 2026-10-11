"""长期记忆第一种：关于用户的几条事实（0019 §四，账 85）。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261012_0016"
down_revision = "20261012_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workbench_memories",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("owner_actor_id", sa.String(128), nullable=False),
        # fact = 关于用户的事实（第一期）；judgement = 用户认下的判断（第二期，所有者再研究）
        sa.Column("kind", sa.String(16), nullable=False, server_default="fact"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("source", pg.JSONB(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "kind in ('fact','judgement')", name="ck_workbench_memories_kind"
        ),
        sa.CheckConstraint(
            "char_length(text) between 1 and 400", name="ck_workbench_memories_text"
        ),
    )
    op.create_index(
        "ix_workbench_memories_owner", "workbench_memories", ["owner_actor_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_workbench_memories_owner", table_name="workbench_memories")
    op.drop_table("workbench_memories")
