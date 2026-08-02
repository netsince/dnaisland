"""站长推荐（站长板块）。

站长可在后台挑选「已通过审核的角色卡」或「未被处罚的普通用户」加入推荐位。
kind + ref_id 唯一确定一个推荐项：kind 为 'card' 时 ref_id 为角色卡 id，
kind 为 'user' 时 ref_id 为用户 id（字符串形式存储，便于统一处理）。
"""
from ..extensions import db


class SiteRecommendation(db.Model):
    __tablename__ = "site_recommendations"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)  # 'card' | 'user'
    ref_id = db.Column(db.String(64), nullable=False, index=True)
    note = db.Column(db.String(200), nullable=True)  # 站长推荐语（可选）
    sort_order = db.Column(db.Integer, server_default="0", nullable=False, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    __table_args__ = (
        db.UniqueConstraint("kind", "ref_id", name="uq_recommend_kind_ref"),
    )
