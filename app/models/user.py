from flask_login import UserMixin

from ..extensions import bcrypt, db
from .punishment import Punishment


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    nickname = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    email_verified = db.Column(db.Boolean, server_default="0", nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    status = db.Column(db.String(20), server_default="active")
    role = db.Column(db.String(20), server_default="user", nullable=False, index=True)

    avatar = db.Column(db.Text, nullable=True)  # 头像（base64 data URL），可空
    bio = db.Column(db.Text, nullable=True)  # 个人简介
    location = db.Column(db.String(80), server_default="", nullable=True)  # 所在地区
    website = db.Column(db.String(200), server_default="", nullable=True)  # 个人网站
    birthday = db.Column(db.Date, nullable=True)  # 生日

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"

    @property
    def active_punishments(self):
        """该用户当前生效的全部处罚（Punishment 对象列表）。"""
        return (
            Punishment.query.filter_by(user_id=self.id, status="active")
            .order_by(Punishment.created_at.desc())
            .all()
        )

    @property
    def active_punishment_types(self):
        return {p.type for p in self.active_punishments}

    def has_punishment(self, ptype: str) -> bool:
        return ptype in self.active_punishment_types

    # —— 各项具体限制（由生效处罚推导）——
    @property
    def is_muted(self) -> bool:
        """禁言：无法发表评论。"""
        return self.has_punishment("mute")

    @property
    def is_profile_banned(self) -> bool:
        """禁止主页被访问：他人打开其主页时仅可见受限提示与处罚列表。"""
        return self.has_punishment("profile_banned")

    @property
    def is_edit_profile_banned(self) -> bool:
        """禁止更改资料。"""
        return self.has_punishment("no_edit_profile")

    @property
    def is_comments_hidden(self) -> bool:
        """屏蔽全部评论：其评论对他人不可见。"""
        return self.has_punishment("hide_comments")

    @property
    def is_cards_hidden(self) -> bool:
        """屏蔽全部角色卡：其角色卡对他人不可见，且不出现在首页。"""
        return self.has_punishment("hide_cards")

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)


class UserFollow(db.Model):
    __tablename__ = "user_follows"

    follower_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    following_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
