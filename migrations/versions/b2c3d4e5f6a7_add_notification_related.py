"""notifications 增加通用关联字段（related_type / related_id）

早先通知只能关联角色卡（related_card_id）。世界卡要做审核通知、被复制通知，
就得有一个不绑死角色卡的关联方式。

* 新增 related_type（card | world | ...）与 related_id；
* **回填**已有数据：凡 related_card_id 非空的都是角色卡关联；
* **related_card_id 不删** —— 已发布的老客户端仍在读它做跳转。

本迁移只加两列并回填，不改动、不删除任何既有列与数据。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "b2c3d4e5f6a7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    """列是否已存在（幂等用）。离线模式无连接可查，一律当作不存在。"""
    if op.get_context().as_sql:
        return False
    inspector = inspect(op.get_bind())
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    if not _has_column("notifications", "related_type"):
        op.add_column(
            "notifications", sa.Column("related_type", sa.String(length=20), nullable=True)
        )
    if not _has_column("notifications", "related_id"):
        op.add_column(
            "notifications", sa.Column("related_id", sa.String(length=36), nullable=True)
        )
    # 回填：已有 related_card_id 的一律视为角色卡关联。WHERE 带 related_type IS NULL，
    # 重复执行不会覆盖新数据。
    op.execute(
        "UPDATE notifications SET related_type='card', related_id=related_card_id "
        "WHERE related_card_id IS NOT NULL AND related_type IS NULL"
    )


def downgrade():
    if _has_column("notifications", "related_id"):
        op.drop_column("notifications", "related_id")
    if _has_column("notifications", "related_type"):
        op.drop_column("notifications", "related_type")
