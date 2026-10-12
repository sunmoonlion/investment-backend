"""Allow each BYOK credential to select an OpenAI-compatible endpoint."""

import sqlalchemy as sa

from alembic import op

revision = "20261012_0018"
down_revision = "20261012_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workbench_credentials",
        sa.Column("base_url", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workbench_credentials", "base_url")
