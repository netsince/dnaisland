"""add card copy_count

Revision ID: q2r3s4t5u6v7
Revises: p1q2r3s4t5u6
Create Date: 2026-08-02

adds cards.copy_count (去重复制量：同人同卡每天最多 +1)
and an index on card_copy_stats to speed up the daily dedup check.
"""
from alembic import op
import sqlalchemy as sa

revision = "q2r3s4t5u6v7"
down_revision = "p1q2r3s4t5u6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) 新增 copy_count 列
    op.add_column(
        "cards",
        sa.Column("copy_count", sa.Integer(), server_default="0", nullable=False),
    )

    # 2) 从已有 card_copy_stats 回填：
    #    复制量口径 = 每张卡 (user_id, 自然日) 的去重计数
    op.execute(
        """
        UPDATE cards c
        SET c.copy_count = COALESCE((
            SELECT COUNT(*) FROM (
                SELECT DISTINCT s.user_id, DATE(s.copied_at) AS d
                FROM card_copy_stats s
                WHERE s.card_id = c.id
            ) AS _dedup
        ), 0)
        WHERE c.id IN (
            SELECT card_id FROM (
                SELECT DISTINCT card_id AS card_id FROM card_copy_stats
            ) AS _cards
        )
        """
    )

    # 3) 加速“同一用户当天是否复制过该卡”的查询
    op.create_index(
        "ix_card_copy_stats_user_card_date",
        "card_copy_stats",
        ["user_id", "card_id", "copied_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_card_copy_stats_user_card_date", table_name="card_copy_stats")
    op.drop_column("cards", "copy_count")
