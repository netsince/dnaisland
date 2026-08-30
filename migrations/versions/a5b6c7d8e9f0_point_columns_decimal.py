"""make point-related columns support decimals

Revision ID: a5b6c7d8e9f0
Revises: i9j0k1l2m3n4
Create Date: 2026-08-10 18:00:00.000000

将积分相关字段从 INTEGER 改为 NUMERIC(10,2)，以支持小数（如 0.5）：
- users.points
- point_transactions.delta / balance_after
- redemption_keys.points
- key_usage_logs.points_gained
- generation_models.points_per_image
- generation_logs.points_spent

整数 → 小数为无损转换，无需额外的数据搬移（MySQL 的 ALTER 会自动完成）。
SQLite 下由 batch_alter_table 重建表完成同样转换。
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.compiler import compiles


@compiles(mysql.LONGTEXT, "sqlite")
def _compile_longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


# revision identifiers, used by Alembic.
revision = 'a5b6c7d8e9f0'
down_revision = 'i9j0k1l2m3n4'
branch_labels = None
depends_on = None

_NUMERIC = sa.Numeric(precision=10, scale=2)


def _fk_toggle(on):
    """SQLite 用 batch_alter_table 重建表时需要临时关外键，MySQL 直接 ALTER 无需且不支持 PRAGMA。

    这里用 try/except 兜底：MySQL 执行 PRAGMA 会报语法错，忽略即可。
    """
    if on is None:
        return
    try:
        op.execute("PRAGMA foreign_keys=%s" % ("ON" if on else "OFF"))
    except Exception:
        pass


def upgrade():
    _fk_toggle(False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('points', existing_type=sa.Integer(), type_=_NUMERIC, existing_nullable=False)

    with op.batch_alter_table('point_transactions', schema=None) as batch_op:
        batch_op.alter_column('delta', existing_type=sa.Integer(), type_=_NUMERIC, existing_nullable=False)
        batch_op.alter_column('balance_after', existing_type=sa.Integer(), type_=_NUMERIC, existing_nullable=False)

    with op.batch_alter_table('redemption_keys', schema=None) as batch_op:
        batch_op.alter_column('points', existing_type=sa.Integer(), type_=_NUMERIC, existing_nullable=False)

    with op.batch_alter_table('key_usage_logs', schema=None) as batch_op:
        batch_op.alter_column('points_gained', existing_type=sa.Integer(), type_=_NUMERIC, existing_nullable=False)

    with op.batch_alter_table('generation_models', schema=None) as batch_op:
        batch_op.alter_column('points_per_image', existing_type=sa.Integer(), type_=_NUMERIC, existing_nullable=False)

    with op.batch_alter_table('generation_logs', schema=None) as batch_op:
        batch_op.alter_column('points_spent', existing_type=sa.Integer(), type_=_NUMERIC, existing_nullable=False)

    _fk_toggle(True)


def downgrade():
    _fk_toggle(False)

    with op.batch_alter_table('generation_logs', schema=None) as batch_op:
        batch_op.alter_column('points_spent', existing_type=_NUMERIC, type_=sa.Integer(), existing_nullable=False)

    with op.batch_alter_table('generation_models', schema=None) as batch_op:
        batch_op.alter_column('points_per_image', existing_type=_NUMERIC, type_=sa.Integer(), existing_nullable=False)

    with op.batch_alter_table('key_usage_logs', schema=None) as batch_op:
        batch_op.alter_column('points_gained', existing_type=_NUMERIC, type_=sa.Integer(), existing_nullable=False)

    with op.batch_alter_table('redemption_keys', schema=None) as batch_op:
        batch_op.alter_column('points', existing_type=_NUMERIC, type_=sa.Integer(), existing_nullable=False)

    with op.batch_alter_table('point_transactions', schema=None) as batch_op:
        batch_op.alter_column('balance_after', existing_type=_NUMERIC, type_=sa.Integer(), existing_nullable=False)
        batch_op.alter_column('delta', existing_type=_NUMERIC, type_=sa.Integer(), existing_nullable=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('points', existing_type=_NUMERIC, type_=sa.Integer(), existing_nullable=False)

    _fk_toggle(True)
