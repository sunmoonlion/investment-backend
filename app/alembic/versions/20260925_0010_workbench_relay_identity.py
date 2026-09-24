"""工作台为每个用户签发的会合点身份（0004-relay 第一期动态登记）与沙箱供给状态（0003 D9）。

relay 令牌只存密文（Fernet，同凭据库密钥）；代理令牌在签发时给用户看一次。沙箱行增加 provisioned_by/relay_user。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20260925_0010"
down_revision = "20260924_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workbench_relay_identities",
        sa.Column("owner_actor_id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("relay_user", sa.String(64), nullable=False, unique=True),
        sa.Column("agent_token_ciphertext", sa.Text(), nullable=False),
        sa.Column("sandbox_token_ciphertext", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workbench_sandboxes",
        sa.Column("relay_user", sa.String(64), nullable=True),
    )
    op.add_column(
        "workbench_sandboxes",
        sa.Column(
            "provisioned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column("workbench_sandboxes", "provisioned")
    op.drop_column("workbench_sandboxes", "relay_user")
    op.drop_table("workbench_relay_identities")
