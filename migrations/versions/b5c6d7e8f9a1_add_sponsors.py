"""add sponsors table and sponsor site config fields

Revision ID: b5c6d7e8f9a1
Revises: a1b2c3d4e5f8
Create Date: 2026-08-05

adds sponsors（赞助者列表）与 site_config 的赞助页面配置字段（开关/标题/富文本说明/按钮链接）
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b5c6d7e8f9a1"
down_revision = "a1b2c3d4e5f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sponsors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("display_name", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.String(length=32), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", name="uq_sponsor_user_id"),
        sa.Index("ix_sponsors_user_id", "user_id"),
        sa.Index("ix_sponsors_sort_order", "sort_order"),
    )
    op.add_column(
        "site_config",
        sa.Column("sponsor_enabled", sa.Boolean(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("site_config", sa.Column("sponsor_title", sa.String(length=200), nullable=True))
    op.add_column("site_config", sa.Column("sponsor_content", sa.Text(), nullable=True))
    op.add_column("site_config", sa.Column("sponsor_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("site_config", "sponsor_url")
    op.drop_column("site_config", "sponsor_content")
    op.drop_column("site_config", "sponsor_title")
    op.drop_column("site_config", "sponsor_enabled")
    op.drop_table("sponsors")
