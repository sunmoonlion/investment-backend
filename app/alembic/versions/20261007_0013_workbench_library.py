"""知识库（SDD 0011 第一期）：用户对自己资料的改名与删除。

清单本身不落表——底稿与交回物已经在 workbench_tasks / workbench_artifacts 里，知识库是对它们的
归档清单，查询时算出来。这张表只记用户动过的那几条：改了名字、从知识库里拿掉。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261007_0013"
down_revision = "20260929_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workbench_library_items",
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", sa.String(200), nullable=False),
        sa.Column("title", sa.String(400), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
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
        sa.PrimaryKeyConstraint("owner_actor_id", "item_id"),
    )


def downgrade() -> None:
    op.drop_table("workbench_library_items")
