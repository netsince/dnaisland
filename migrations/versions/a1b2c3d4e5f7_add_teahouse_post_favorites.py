"""add teahouse_post_favorites table

Revision ID: a1b2c3d4e5f7
Revises: z3a4b5c6d7e8
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "a1b2c3d4e5f7"
down_revision = "z3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())

    if "teahouse_post_favorites" not in existing:
        op.create_table(
            "teahouse_post_favorites",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("post_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["post_id"], ["teahouse_posts.id"]),
            sa.PrimaryKeyConstraint("user_id", "post_id"),
            mysql_charset="utf8mb4",
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())
    if "teahouse_post_favorites" in existing:
        op.drop_table("teahouse_post_favorites")
