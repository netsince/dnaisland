"""add teahouse_topics and teahouse_post_topics tables

Revision ID: a1b2c3d4e5f8
Revises: a1b2c3d4e5f7
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "a1b2c3d4e5f8"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())

    if "teahouse_topics" not in existing:
        op.create_table(
            "teahouse_topics",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=50), nullable=False),
            sa.Column("post_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
            mysql_charset="utf8mb4",
        )

    if "teahouse_post_topics" not in existing:
        op.create_table(
            "teahouse_post_topics",
            sa.Column("post_id", sa.Integer(), nullable=False),
            sa.Column("topic_id", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["post_id"], ["teahouse_posts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["topic_id"], ["teahouse_topics.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("post_id", "topic_id"),
            mysql_charset="utf8mb4",
        )


def downgrade():
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())
    if "teahouse_post_topics" in existing:
        op.drop_table("teahouse_post_topics")
    if "teahouse_topics" in existing:
        op.drop_table("teahouse_topics")
