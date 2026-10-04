"""add users.session_epoch

给 `users` 加一列**会话代数**：每改一次密码 +1，网页会话与 App JWT 都带上它并在请求时比对，
不一致即视为失效 —— 于是「改密码 / 找回密码」会踢掉该用户所有旧设备。

为什么要这一列：应用用的是 Flask-Login 的**签名 Cookie** 与**无状态 JWT**，服务端不存会话表，
光改密码不会让已经发出去的凭证失效（账号被盗后对方那台仍在线）。

安全性说明（改动本身不会踢人下线）：
* 列是 NOT NULL + server_default '0'，存量行自动补 0，**无需回填**；
* 校验口径是「缺失/为 0 一律当 0」，所以部署瞬间所有旧 Cookie / 旧 token 依然有效；
  只有**真的改过密码**（epoch 变成 ≥1）的用户，手上的旧凭证才会失效。

本迁移只新增一列，不改动、不删除任何既有列/数据。
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "e1f2a3b4c5d7"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    """列是否已存在（幂等用）。离线模式无连接可查，一律当作不存在。"""
    if op.get_context().as_sql:
        return False
    inspector = inspect(op.get_bind())
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade():
    if not _has_column("users", "session_epoch"):
        op.add_column(
            "users",
            sa.Column(
                "session_epoch",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade():
    if _has_column("users", "session_epoch"):
        op.drop_column("users", "session_epoch")
