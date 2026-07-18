from sqlalchemy import or_
from sqlalchemy.dialects.mysql import LONGTEXT

from ..extensions import db


class Card(db.Model):
    __tablename__ = "cards"

    # 平台自动分配的主键（UUID），不读取客户端 JSON 中的 id
    id = db.Column(db.String(36), primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    gender = db.Column(db.String(20), server_default="无性")
    persona = db.Column(db.Text, nullable=False, server_default="")
    intro = db.Column(db.Text, server_default="")
    opening = db.Column(db.Text, server_default="")
    original_link = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime, server_default=db.func.now(), onupdate=db.func.now()
    )
    status = db.Column(db.String(20), server_default="pending")
    is_hidden = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    view_count = db.Column(db.Integer, server_default="0")

    author = db.relationship("User", backref="cards")

    @classmethod
    def visible_to(cls, viewer=None):
        """信息层面的可见性过滤：返回对 viewer 可见的「已通过且未隐藏」角色卡查询。

        自动排除处于 hide_cards 处罚下作者的卡片；viewer 为作者本人或超级管理员时不过滤。
        单卡详情访问的权限（如 card_detail 的 404 拦截）仍由路由单独把关，本方法仅用于列表检索。
        """
        from .punishment import Punishment
        from .user import User

        q = (
            cls.query.join(User, cls.author_id == User.id)
            .filter(
                cls.status == "approved",
                cls.is_hidden.is_(False),
                User.status != "admin_del",
            )
        )
        hidden_ids = (
            db.session.query(Punishment.user_id)
            .filter(Punishment.status == "active", Punishment.type == "hide_cards")
            .distinct()
        )
        if viewer is not None and getattr(viewer, "is_authenticated", False):
            if viewer.is_super_admin:
                return q
            # 排除他人中被屏蔽作者的卡；本人自己的卡（即便被处罚）仍可见
            q = q.filter(or_(cls.author_id == viewer.id, cls.author_id.notin_(hidden_ids)))
        else:
            q = q.filter(cls.author_id.notin_(hidden_ids))
        return q


class CardTag(db.Model):
    __tablename__ = "card_tags"

    card_id = db.Column(db.String(36), db.ForeignKey("cards.id"), primary_key=True)
    tag = db.Column(db.String(50), primary_key=True, index=True)


class CardDialogueStyle(db.Model):
    __tablename__ = "card_dialogue_styles"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.String(36), db.ForeignKey("cards.id"), nullable=False)
    turn_index = db.Column(db.Integer, nullable=False, server_default="0")
    user_text = db.Column(db.Text, server_default="")
    assistant_text = db.Column(db.Text, server_default="")


class CardImage(db.Model):
    __tablename__ = "card_images"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.String(36), db.ForeignKey("cards.id"), nullable=False)
    slot = db.Column(db.String(20), nullable=False)  # square | landscape | portrait
    data = db.Column(LONGTEXT, nullable=False)  # base64 data URI


class CardLike(db.Model):
    __tablename__ = "card_likes"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    card_id = db.Column(db.String(36), db.ForeignKey("cards.id"), primary_key=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class CardFavorite(db.Model):
    __tablename__ = "card_favorites"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    card_id = db.Column(db.String(36), db.ForeignKey("cards.id"), primary_key=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.String(36), db.ForeignKey("cards.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    # 评论审核（先发后审）：is_hidden=被拒绝隐藏；moderated=是否已进入审核流程处理过
    is_hidden = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    moderated = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    author = db.relationship("User", backref="comments")
