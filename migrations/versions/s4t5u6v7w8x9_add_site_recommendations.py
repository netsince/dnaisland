"""add site recommendations

Revision ID: s4t5u6v7w8x9
Revises: r3s4t5u6v7w8
Create Date: 2026-08-02

adds site_recommendations (站长推荐板块)
"""
from alembic import op
import sqlalchemy as sa

revision = "s4t5u6v7w8x9"
down_revision = "r3s4t5u6v7w8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_recommendations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("ref_id", sa.String(length=64), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("kind", "ref_id", name="uq_recommend_kind_ref"),
        sa.Index("ix_site_recommendations_ref_id", "ref_id"),
        sa.Index("ix_site_recommendations_sort_order", "sort_order"),
    )


def downgrade() -> None:
    op.drop_table("site_recommendations")
