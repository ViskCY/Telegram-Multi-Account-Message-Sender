"""Add rich content columns for message templates."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20240908_add_rich_template_spans"
down_revision = None
branch_labels = None
depends_on = None

_SPAN_FLAGS = ("bold", "italic", "underline", "strikethrough", "code", "spoiler")


def _default_span(text: str = "", emoji_id: str | None = None) -> dict:
    span: dict = {"emoji_id": emoji_id, "fallback_text": text, "link": None}
    for flag in _SPAN_FLAGS:
        span[flag] = False
    return span


def _plain_text_to_spans(text: str | None) -> list[dict]:
    if not text:
        return []
    return [_default_span(text)]


def upgrade() -> None:
    op.add_column("templates", sa.Column("rich_body", sa.JSON(), nullable=True))
    op.add_column("templates", sa.Column("rich_caption", sa.JSON(), nullable=True))

    connection = op.get_bind()
    templates_table = sa.table(
        "templates",
        sa.column("id", sa.Integer),
        sa.column("rich_body", sa.JSON),
        sa.column("rich_caption", sa.JSON),
    )

    result = connection.execute(sa.text("SELECT id, body, caption FROM templates"))
    for row in result.mappings():
        rich_body = _plain_text_to_spans(row.get("body"))
        rich_caption = _plain_text_to_spans(row.get("caption"))
        connection.execute(
            templates_table.update()
            .where(templates_table.c.id == row["id"])
            .values(
                rich_body=rich_body or None,
                rich_caption=rich_caption or None,
            )
        )


def downgrade() -> None:
    op.drop_column("templates", "rich_caption")
    op.drop_column("templates", "rich_body")
