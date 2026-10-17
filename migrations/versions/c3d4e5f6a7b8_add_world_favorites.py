"""add world_favorites

世界卡收藏。与 card_favorites 同构（user_id + world_id 复合主键）。

为什么世界卡做收藏而不做点赞：收藏 = 「我以后要导入它」，正好对上世界书的
使用方式；点赞在这个形态里没有对应的动作。

本迁移只新增一张表，不改动、不删除任何既有列与数据。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    """表是否已存在（幂等用）。离线模式无连接可查，一律当作不存在。"""
    if op.get_context().as_sql:
        return False
    inspector = inspect(op.get_bind())
    return table in inspector.get_table_names()


def upgrade():
    if not _has_table("world_favorites"):
        op.create_table(
            "world_favorites",
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("world_id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["world_id"], ["world_cards.id"]),
            sa.PrimaryKeyConstraint("user_id", "world_id"),
        )


def downgrade():
    if _has_table("world_favorites"):
        op.drop_table("world_favorites")
