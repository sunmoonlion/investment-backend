"""align UUID primary-key defaults with the ORM

Revision ID: 20260811_0005
Revises: 20260809_0004
Create Date: 2026-08-11
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260811_0005"
down_revision = "20260809_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "auth_user",
        "id",
        server_default=sa.text("gen_random_uuid()"),
    )


def downgrade() -> None:
    op.alter_column("auth_user", "id", server_default=None)
