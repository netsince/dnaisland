"""widen point columns to DECIMAL(30,10) for 10-decimal precision

Revision ID: d8e9f0a1b2c3
Revises: a5b6c7d8e9f0
Create Date: 2026-09-30 12:00:00.000000

把积分相关字段从 NUMERIC(10,2) 扩宽到 NUMERIC(30,10)（20 位整数 + 10 位小数）：
- users.points
- point_transactions.delta / balance_after
- redemption_keys.points
- key_usage_logs.points_gained
- generation_models.points_per_image
- generation_logs.points_spent

精度常量单点定义见 app/constants.py（POINT_PRECISION / POINT_SCALE），本迁移必须与之一致。

10,2 -> 30,10 是无损扩宽（整数位 8 -> 20，小数位 2 -> 10），无需搬移数据。

回滚风险（务必先读）：
    downgrade 会窄化回 NUMERIC(10,2)。MariaDB/MySQL 在 ALTER 时对超出 (10,2) 的既有数据
    会四舍五入或报错（整数位 > 8 位时报 "Out of range value"）。回滚前必须先执行：
        SELECT COUNT(*) FROM users WHERE points >= 100000000 OR points <= -100000000;
        （以及 point_transactions / redemption_keys / key_usage_logs /
          generation_models / generation_logs 的同名列）
    确认无超范围数据后再回滚，否则会丢失精度或失败。
"""
import contextlib

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.compiler import compiles


@compiles(mysql.LONGTEXT, "sqlite")
def _compile_longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


# revision identifiers, used by Alembic.
revision = 'd8e9f0a1b2c3'
down_revision = 'a5b6c7d8e9f0'
branch_labels = None
depends_on = None

# 必须与 app/constants.py 的 POINT_PRECISION / POINT_SCALE 保持一致。
_NEW = sa.Numeric(precision=30, scale=10)
_OLD = sa.Numeric(precision=10, scale=2)

_TABLE_COLUMNS = [
    ('users', ['points']),
    ('point_transactions', ['delta', 'balance_after']),
    ('redemption_keys', ['points']),
    ('key_usage_logs', ['points_gained']),
    ('generation_models', ['points_per_image']),
    ('generation_logs', ['points_spent']),
]


def _fk_toggle(on):
    """SQLite 用 batch_alter_table 重建表时需要临时关外键；MySQL 执行 PRAGMA 会报错，忽略。"""
    if on is None:
        return
    # MariaDB/MySQL 执行 PRAGMA 会报语法错，这里是最佳努力，忽略即可。
    with contextlib.suppress(Exception):
        op.execute("PRAGMA foreign_keys=%s" % ("ON" if on else "OFF"))


def _alter_all(from_type, to_type):
    for table, columns in _TABLE_COLUMNS:
        with op.batch_alter_table(table, schema=None) as batch_op:
            for column in columns:
                batch_op.alter_column(
                    column, existing_type=from_type, type_=to_type, existing_nullable=False
                )


def upgrade():
    _fk_toggle(False)
    _alter_all(_OLD, _NEW)
    _fk_toggle(True)


def downgrade():
    _fk_toggle(False)
    _alter_all(_NEW, _OLD)
    _fk_toggle(True)
