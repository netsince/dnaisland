"""赞助者（赞助页面展示）。

站长可在后台挑选「未被处罚的普通用户」加入赞助者列表，并为其配置
累计赞助数额与显示名。user_id 即论坛 UID（User.id），点击可跳转到对应用户主页。
"""
from ..extensions import db


class Sponsor(db.Model):
    __tablename__ = "sponsors"

    id = db.Column(db.Integer, primary_key=True)
    # 论坛 UID = User.id，唯一（同一用户只展示一次）
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    # 显示名：默认取用户昵称，站长可自定义
    display_name = db.Column(db.String(64), nullable=False)
    # 累计赞助数额（字符串，便于展示「¥66.6」等自定义格式）
    amount = db.Column(db.String(32), nullable=True)
    # 排序（越小越靠前）
    sort_order = db.Column(db.Integer, server_default="0", nullable=False, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("user_id", name="uq_sponsor_user_id"),
    )

    # sponsors 表有两个外键指向 users（user_id=被赞助者、created_by=录入者），
    # 必须显式指定 foreign_keys 为 user_id，否则 SQLAlchemy 无法确定连接条件，
    # 首次 mapper 配置时会抛 AmbiguousForeignKeysError。
    user = db.relationship("User", foreign_keys=[user_id], backref="sponsors")
