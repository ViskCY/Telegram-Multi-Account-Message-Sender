"""Add entity_spans column to templates table."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20240910_add_template_entity_spans"
down_revision = "20240909_add_account_premium_flag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column("entity_spans", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("templates", "entity_spans")
