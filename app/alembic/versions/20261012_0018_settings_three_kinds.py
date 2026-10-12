"""设置分三类（0021，账 93）：模型 key 加接入地址；偏好加 MCP 来源开关。"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "20261012_0018"
down_revision = "20261012_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 自建或代理的 OpenAI 兼容端点；空 = 用厂商默认（部署配置里的 provider_base_url）
    op.add_column(
        "workbench_credentials", sa.Column("base_url", sa.String(512), nullable=True)
    )
    # Codex 在云端能用哪些数据源：{"knowledge": true}；空 = 全开
    op.add_column(
        "workbench_user_prefs", sa.Column("mcp_sources", pg.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workbench_user_prefs", "mcp_sources")
    op.drop_column("workbench_credentials", "base_url")
