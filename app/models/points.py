"""点数（积分）与兑换码（Key）相关模型。

- PointTransaction: 用户点数变化明细（每条记录含变化前后余额，便于审计）。
- RedemptionKey:    兑换码本体，可配置点数、使用上限、单人上限、有效期。
- KeyUsageLog:      兑换码的每次兑换尝试记录（成功/失败及原因）。
"""

from datetime import datetime, date

from ..extensions import db


class PointTransaction(db.Model):
    __tablename__ = "point_transactions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    delta = db.Column(db.Integer, nullable=False)  # 变化量：正为增加，负为扣减
    balance_after = db.Column(db.Integer, nullable=False)  # 变化后余额
    reason = db.Column(db.String(120), nullable=False, default="")  # 说明
    source = db.Column(
        db.String(20), nullable=False, default="system"
    )  # redeem / admin / consume / system
    related_key = db.Column(db.String(64), nullable=True)  # 关联兑换码（冗余）
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def __repr__(self):
        return f"<PointTransaction user={self.user_id} {self.delta:+d}>"


class RedemptionKey(db.Model):
    __tablename__ = "redemption_keys"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), nullable=False, unique=True, index=True)
    points = db.Column(db.Integer, nullable=False, default=0)  # 兑换可得点数
    max_uses = db.Column(db.Integer, nullable=False, default=1)  # 最多使用次数
    per_user_limit = db.Column(
        db.Integer, nullable=False, default=1
    )  # 同一用户最多使用次数
    used_count = db.Column(db.Integer, nullable=False, server_default="0", default=0)
    valid_from = db.Column(db.Date, nullable=True)  # 有效期起（含）
    valid_to = db.Column(db.Date, nullable=True)  # 有效期止（含）
    active = db.Column(db.Boolean, nullable=False, server_default="1", default=True)
    batch = db.Column(db.String(40), nullable=True, index=True)  # 生成批次标识
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def is_valid_now(self):
        """当前是否处于有效期（忽略 active / 次数限制）。"""
        today = date.today()
        if self.valid_from and today < self.valid_from:
            return False
        if self.valid_to and today > self.valid_to:
            return False
        return True

    def __repr__(self):
        return f"<RedemptionKey {self.code} +{self.points}>"


class KeyUsageLog(db.Model):
    __tablename__ = "key_usage_logs"

    id = db.Column(db.Integer, primary_key=True)
    key_id = db.Column(
        db.Integer, db.ForeignKey("redemption_keys.id"), nullable=True, index=True
    )
    code = db.Column(db.String(64), nullable=False, index=True)  # 冗余，便于审计
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True, index=True
    )
    points_gained = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(10), nullable=False, default="success")  # success/fail
    note = db.Column(db.String(200), nullable=True)  # 失败原因
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def __repr__(self):
        return f"<KeyUsageLog {self.code} {self.status}>"
