"""add sticker_series and stickers tables

Revision ID: z3a4b5c6d7e8
Revises: y2z3a4b5c6d7
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy import inspect

revision = "z3a4b5c6d7e8"
down_revision = "y2z3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())

    if "sticker_series" not in existing:
        op.create_table(
            "sticker_series",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(60), nullable=False),
            sa.Column("slug", sa.String(60), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("slug"),
            mysql_charset="utf8mb4",
        )

    if "stickers" not in existing:
        op.create_table(
            "stickers",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("code", sa.String(60), nullable=False),
            sa.Column(
                "series_id",
                sa.Integer(),
                sa.ForeignKey("sticker_series.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("image_data", mysql.LONGTEXT(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code"),
            mysql_charset="utf8mb4",
        )
        op.create_index("ix_stickers_series_id", "stickers", ["series_id"])


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())
    if "stickers" in existing:
        op.drop_table("stickers")
    if "sticker_series" in existing:
        op.drop_table("sticker_series")
