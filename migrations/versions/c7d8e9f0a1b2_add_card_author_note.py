"""add cards.author_note and cards.author_note_interval

Revision ID: c7d8e9f0a1b2
Revises: b1f2f3f4f5f6
Create Date: 2026-08-05

adds cards.author_note (角色卡绑定的作者注释，可选) 与 cards.author_note_interval
（注入间隔，默认 0 表示禁用）。支持社区识别/透传客户端角色卡的作者注释。
"""
from alembic import op
import sqlalchemy as sa

revision = "c7d8e9f0a1b2"
down_revision = "b1f2f3f4f5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cards",
        sa.Column("author_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "cards",
        sa.Column(
            "author_note_interval",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("cards", "author_note_interval")
    op.drop_column("cards", "author_note")
