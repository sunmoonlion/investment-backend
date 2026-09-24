"""工作台设置面（0002-web「设置」「底稿」）：用户偏好（模型、审批策略）与 BYOK 凭据登记。

凭据只存密文（Fernet，密钥在服务端配置），接口永不回显；撤换只改状态。底稿的结论栏是用户草稿，落 workbench_artifacts（name=conclusion, kind=user_draft）。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20260924_0009"
down_revision = "20260924_0008"
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
        "workbench_user_prefs",
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column(
            "approval_policy",
            sa.String(32),
            nullable=False,
            server_default="on-request",
        ),
        _ts("updated_at"),
    )
    op.create_table(
        "workbench_credentials",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("sandbox_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("hint", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        _ts("created_at"),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_workbench_credentials_owner",
        "workbench_credentials",
        ["owner_actor_id", "status"],
    )


def downgrade():
    op.drop_table("workbench_credentials")
    op.drop_table("workbench_user_prefs")
