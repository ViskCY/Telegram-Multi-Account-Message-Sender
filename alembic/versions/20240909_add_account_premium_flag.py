"""Add premium flag to accounts."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20240909_add_account_premium_flag"
down_revision = "20240908_add_rich_template_spans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("is_premium", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("accounts", "is_premium", server_default=None)


def downgrade() -> None:
    op.drop_column("accounts", "is_premium")
