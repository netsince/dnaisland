from ..extensions import db


# 处罚类型常量（可叠加）
PUNISHMENT_TYPES = {
    "mute": "禁言",
    "profile_banned": "禁止主页被访问",
    "reset_profile": "重置资料",
    "no_edit_profile": "禁止更改资料",
    "hide_comments": "屏蔽全部评论",
    "hide_cards": "屏蔽全部角色卡",
    "clear_avatar": "清除头像",
}

# 申诉状态
APPEAL_NONE = "none"
APPEAL_PENDING = "pending"
APPEAL_ACCEPTED = "accepted"
APPEAL_REJECTED = "rejected"


class Punishment(db.Model):
    __tablename__ = "punishments"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    type = db.Column(db.String(30), nullable=False, index=True)
    reason = db.Column(db.Text, server_default="")  # 管理员施加处罚的理由
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    expires_at = db.Column(db.DateTime, nullable=True)  # null = 永久
    handled_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    status = db.Column(db.String(20), server_default="active", nullable=False)  # active | revoked

    # 申诉（每个处罚仅可申诉一次）
    appealed = db.Column(db.Boolean, server_default="0", nullable=False)
    appeal_reason = db.Column(db.Text, nullable=True)
    appeal_status = db.Column(db.String(20), server_default=APPEAL_NONE, nullable=False)
    appeal_at = db.Column(db.DateTime, nullable=True)
    appeal_handled_at = db.Column(db.DateTime, nullable=True)
    appeal_handled_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    appeal_reply = db.Column(db.Text, nullable=True)

    user = db.relationship(
        "User", foreign_keys=[user_id], backref="punishments"
    )
    handler = db.relationship("User", foreign_keys=[handled_by])

    @classmethod
    def label(cls, t: str) -> str:
        return PUNISHMENT_TYPES.get(t, t)

    @property
    def type_label(self) -> str:
        return self.label(self.type)

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def can_appeal(self) -> bool:
        """仅当处罚生效且尚未申诉过时，可申诉。"""
        return self.is_active and not self.appealed
