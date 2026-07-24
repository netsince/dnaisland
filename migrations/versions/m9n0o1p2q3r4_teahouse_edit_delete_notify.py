"""teahouse edit/delete and like-notify preference

Revision ID: m9n0o1p2q3r4
Revises: l8m9n0p1q2r3
Create Date: 2026-07-24 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "m9n0o1p2q3r4"
# 合并两条历史分支（l8m9n0p1q2r3 与 2eea28dec912）为单一 head
down_revision = ("l8m9n0p1q2r3", "2eea28dec912")
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "teahouse_posts",
        sa.Column("edited_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "teahouse_posts",
        sa.Column("is_deleted", sa.Boolean(), server_default="0", nullable=False),
    )
    op.add_column(
        "teahouse_posts",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("notify_like", sa.Boolean(), server_default="1", nullable=False),
    )


def downgrade():
    op.drop_column("users", "notify_like")
    op.drop_column("teahouse_posts", "deleted_at")
    op.drop_column("teahouse_posts", "is_deleted")
    op.drop_column("teahouse_posts", "edited_at")
