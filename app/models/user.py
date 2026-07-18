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

    # 平台认证：由管理员授予，区别于 email_verified（邮箱验证）
    verified = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    verified_label = db.Column(db.String(50), nullable=True)  # 认证说明，如「官方」「知名创作者」

    @property
    def is_super_admin(self) -> bool:
        return self.role == "super_admin"

    @property
    def is_deleted(self) -> bool:
        """账号已被管理员删除（软删除，status=admin_del）。"""
        return self.status == "admin_del"

    @property
    def is_cancelled(self) -> bool:
        """账号已注销（status=user_del）。角色卡/茶馆/评论保留，作者名显示「已注销用户」。"""
        return self.status == "user_del"

    @property
    def is_mourning(self) -> bool:
        """纪念状态（status=mourning）。禁止写操作与登录，主页挂纪念横幅。"""
        return self.status == "mourning"

    @property
    def is_locked(self) -> bool:
        """被封禁/注销/进入纪念：禁止登录与一切写操作（登录后自动登出）。"""
        return self.status in ("admin_del", "user_del", "mourning")

    @property
    def display_name(self) -> str:
        """对外展示的昵称：已删除→「已删除用户」，已注销→「已注销用户」。"""
        if self.is_deleted:
            return "已删除用户"
        if self.is_cancelled:
            return "已注销用户"
        return self.nickname

    @property
    def is_verified(self) -> bool:
        """平台认证用户：由管理员授予的认证标记。"""
        return bool(self.verified)

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
