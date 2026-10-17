"""add world cards (world_cards / world_tags / world_copy_stats)

社区「世界卡」：把本地世界（World.toJson() 的原文）发布到社区。

三条设计要点（改动前请先读）：

* payload 是**原文**，服务端不解析词条结构。词条字段（正则激活键 keyRegex、
  递归扫描 recursive、冷却/延迟轮数、装饰符 decorator、排序 order……）还会
  继续长；服务端一旦去解析，每加一个字段都要改后端 + 迁移，那才是真正的限制。
* 用 LONGTEXT 而不是 TEXT：MySQL 的 TEXT 只有 64KB，几百 KB 的世界书会直接
  存不进去。这里**不设产品级体积上限**，让基础设施的天花板说话（请求体另有
  MAX_CONTENT_LENGTH = 16MB 兜底）。
* world_copy_stats 与 card_copy_stats 同构：每次「复制世界卡」（取一次原文）
  记一条，含世界名与用户名快照 + 来源 IP，用于版权溯源。

本迁移只新增三张表，不改动、不删除任何既有列与数据。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql

revision = "f1a2b3c4d5e6"
down_revision = "e1f2a3b4c5d7"
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    """表是否已存在（幂等用）。离线模式无连接可查，一律当作不存在。"""
    if op.get_context().as_sql:
        return False
    inspector = inspect(op.get_bind())
    return table in inspector.get_table_names()


def upgrade():
    if not _has_table("world_cards"):
        op.create_table(
            "world_cards",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("author_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("summary", sa.Text(), server_default=""),
            sa.Column(
                "entry_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "payload_bytes", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("payload", mysql.LONGTEXT(), nullable=False),
            sa.Column("view_count", sa.Integer(), server_default="0"),
            sa.Column("copy_count", sa.Integer(), server_default="0"),
            sa.Column("status", sa.String(length=20), server_default="pending"),
            sa.Column(
                "is_hidden", sa.Boolean(), nullable=False, server_default="0"
            ),
            sa.Column("content_hash", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_world_cards_is_hidden", "world_cards", ["is_hidden"]
        )
        op.create_index(
            "ix_world_cards_content_hash", "world_cards", ["content_hash"]
        )

    if not _has_table("world_tags"):
        op.create_table(
            "world_tags",
            sa.Column("world_id", sa.String(length=36), nullable=False),
            sa.Column("tag", sa.String(length=50), nullable=False),
            sa.ForeignKeyConstraint(["world_id"], ["world_cards.id"]),
            sa.PrimaryKeyConstraint("world_id", "tag"),
        )
        op.create_index("ix_world_tags_tag", "world_tags", ["tag"])

    if not _has_table("world_copy_stats"):
        op.create_table(
            "world_copy_stats",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("world_id", sa.String(length=36), nullable=False),
            sa.Column(
                "world_name", sa.String(length=120), nullable=False, server_default=""
            ),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column(
                "username", sa.String(length=120), nullable=False, server_default=""
            ),
            sa.Column(
                "copied_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("copier_ip", sa.String(length=64), nullable=True),
            sa.ForeignKeyConstraint(["world_id"], ["world_cards.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_world_copy_stats_world_id", "world_copy_stats", ["world_id"]
        )
        op.create_index(
            "ix_world_copy_stats_user_id", "world_copy_stats", ["user_id"]
        )
        op.create_index(
            "ix_world_copy_stats_copied_at", "world_copy_stats", ["copied_at"]
        )


def downgrade():
    if _has_table("world_copy_stats"):
        op.drop_table("world_copy_stats")
    if _has_table("world_tags"):
        op.drop_table("world_tags")
    if _has_table("world_cards"):
        op.drop_table("world_cards")
