"""add card seed

Revision ID: r3s4t5u6v7w8
Revises: q2r3s4t5u6v7
Create Date: 2026-08-02

adds cards.seed (角色卡语音合成 seed，可空；决定复制/导出后的音色)
"""
from alembic import op
import sqlalchemy as sa

revision = "r3s4t5u6v7w8"
down_revision = "q2r3s4t5u6v7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cards",
        sa.Column("seed", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cards", "seed")
