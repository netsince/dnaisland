"""add teahouse_post_images table

Revision ID: y2z3a4b5c6d7
Revises: x1y2z3a4b5c6
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy import inspect

revision = "y2z3a4b5c6d7"
down_revision = "x1y2z3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "teahouse_post_images" in inspector.get_table_names():
        return
    op.create_table(
        "teahouse_post_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "post_id",
            sa.Integer(),
            sa.ForeignKey("teahouse_posts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("image_data", mysql.LONGTEXT(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_teahouse_post_images_post_id",
        "teahouse_post_images",
        ["post_id"],
    )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    if "teahouse_post_images" not in inspector.get_table_names():
        return
    op.drop_table("teahouse_post_images")
