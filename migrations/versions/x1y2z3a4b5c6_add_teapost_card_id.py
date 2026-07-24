"""add teapost card_id

Revision ID: x1y2z3a4b5c6
Revises: m9n0o1p2q3r4
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "x1y2z3a4b5c6"
down_revision = "m9n0o1p2q3r4"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "teahouse_posts" not in inspector.get_table_names():
        return
    cols = [c["name"] for c in inspector.get_columns("teahouse_posts")]
    if "card_id" not in cols:
        op.add_column(
            "teahouse_posts",
            sa.Column(
                "card_id",
                sa.String(length=36),
                sa.ForeignKey("cards.id"),
                nullable=True,
            ),
        )
    if "quote_post_id" not in cols:
        op.add_column(
            "teahouse_posts",
            sa.Column(
                "quote_post_id",
                sa.Integer(),
                sa.ForeignKey("teahouse_posts.id"),
                nullable=True,
            ),
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "teahouse_posts" not in inspector.get_table_names():
        return
    cols = [c["name"] for c in inspector.get_columns("teahouse_posts")]
    if "quote_post_id" in cols:
        op.drop_column("teahouse_posts", "quote_post_id")
    if "card_id" in cols:
        op.drop_column("teahouse_posts", "card_id")
