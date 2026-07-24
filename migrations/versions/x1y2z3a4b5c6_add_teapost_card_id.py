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
    if "tea_post" not in inspector.get_table_names():
        return
    cols = [c["name"] for c in inspector.get_columns("tea_post")]
    if "card_id" not in cols:
        op.add_column(
            "tea_post",
            sa.Column(
                "card_id",
                sa.String(length=36),
                sa.ForeignKey("cards.id"),
                nullable=True,
            ),
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "tea_post" not in inspector.get_table_names():
        return
    cols = [c["name"] for c in inspector.get_columns("tea_post")]
    if "card_id" in cols:
        op.drop_column("tea_post", "card_id")
