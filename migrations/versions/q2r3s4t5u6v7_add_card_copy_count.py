"""add card copy_count

Revision ID: q2r3s4t5u6v7
Revises: p1q2r3s4t5u6
Create Date: 2026-08-02

adds cards.copy_count (去重复制量：同人同卡每天最多 +1)
and an index on card_copy_stats to speed up the daily dedup check.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "q2r3s4t5u6v7"
down_revision = "p1q2r3s4t5u6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in inspect(bind).get_columns("cards")}

    # 1) 新增 copy_count 列（幂等：若之前部分执行已建过列则跳过）
    if "copy_count" not in existing:
        op.add_column(
            "cards",
            sa.Column("copy_count", sa.Integer(), server_default="0", nullable=False),
        )

    # 2) 从已有 card_copy_stats 回填：
    #    复制量口径 = 每张卡 (user_id, 自然日) 的去重计数。
    #    MySQL 禁止在 UPDATE 子查询里引用被更新表，故改用 JOIN 派生表的方式。
    op.execute(
        """
        UPDATE cards c
        JOIN (
            SELECT card_id, COUNT(*) AS cnt FROM (
                SELECT DISTINCT card_id, user_id, DATE(copied_at) AS d
                FROM card_copy_stats
            ) AS _dd
            GROUP BY card_id
        ) AS t ON c.id = t.card_id
        SET c.copy_count = t.cnt
        """
    )

    # 3) 加速“同一用户当天是否复制过该卡”的查询（幂等）
    existing_idx = {i["name"] for i in inspect(bind).get_indexes("card_copy_stats")}
    if "ix_card_copy_stats_user_card_date" not in existing_idx:
        op.create_index(
            "ix_card_copy_stats_user_card_date",
            "card_copy_stats",
            ["user_id", "card_id", "copied_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_card_copy_stats_user_card_date", table_name="card_copy_stats")
    op.drop_column("cards", "copy_count")
