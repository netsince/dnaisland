"""add card content_hash for duplicate-submission dedup

Revision ID: c6d7e8f9a1b2
Revises: b5c6d7e8f9a1
Create Date: 2026-08-05

为 cards 表增加 content_hash（内容指纹），配合 create_card_from_payload 的
幂等去重：同一作者重复提交相同内容的卡片时复用已有待审核卡，避免重复入库。
"""
from alembic import op
import sqlalchemy as sa

revision = "c6d7e8f9a1b2"
down_revision = "b5c6d7e8f9a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "cards",
        sa.Column("content_hash", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_cards_content_hash", "cards", ["content_hash"])


def downgrade() -> None:
    op.drop_index("ix_cards_content_hash", table_name="cards")
    op.drop_column("cards", "content_hash")
