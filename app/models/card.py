from sqlalchemy import and_, or_
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
    # 头图封面焦点（脸部位置），格式 "x%,y%" 如 "50,30"，留空则居中
    cover_focus = db.Column(db.String(16), nullable=True)

    author = db.relationship("User", backref="cards")
    images = db.relationship("CardImage", backref="card")

    @classmethod
    def visible_to(cls, viewer=None):
        """信息层面的可见性过滤：返回对 viewer 可见的角色卡查询。

        - 作者本人：可查看/关联自己所有的卡（包含 pending 待审核卡）
        - 超级管理员：可查看所有未硬删除的卡
        - 普通访客/其他用户：仅可查看 status == 'approved' 且未隐藏、未处罚作者的卡
        """
        from .punishment import Punishment
        from .user import User

        q = cls.query.join(User, cls.author_id == User.id).filter(
            cls.is_hidden.is_(False),
            User.status != "admin_del",
        )
        hidden_ids = (
            db.session.query(Punishment.user_id)
            .filter(Punishment.status == "active", Punishment.type == "hide_cards")
            .distinct()
        )
        if viewer is not None and getattr(viewer, "is_authenticated", False):
            if viewer.is_super_admin:
                return q
            # 本人可访问自己的卡（含 pending 状态与被处罚时期）；他人卡必须为 approved 且作者未处于屏蔽名单
            return q.filter(
                or_(
                    cls.author_id == viewer.id,
                    and_(
                        cls.status == "approved",
                        cls.author_id.notin_(hidden_ids),
                    ),
                )
            )
        return q.filter(cls.status == "approved", cls.author_id.notin_(hidden_ids))

    @property
    def is_public(self):
        """单卡是否对公众可见：已通过、未隐藏，且作者未被封禁/隐藏卡片。

        用于 card_detail / card_export 等单卡访问的 404 拦截，与 visible_to 口径一致。
        """
        author = self.author
        return (
            self.status == "approved"
            and not self.is_hidden
            and not (author and (author.is_cards_hidden or author.is_profile_banned))
        )


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
    data = db.deferred(db.Column(LONGTEXT, nullable=False))  # base64 data URI


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


class CommentLike(db.Model):
    __tablename__ = "comment_likes"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey("comments.id"), primary_key=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class Comment(db.Model):
    __tablename__ = "comments"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.String(36), db.ForeignKey("cards.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    image_data = db.deferred(db.Column(LONGTEXT, nullable=True))  # 评论图片（WebP base64 data URL），可空
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    # 评论审核（先发后审）：is_hidden=被拒绝隐藏；moderated=是否已进入审核流程处理过
    is_hidden = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    moderated = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    is_pinned = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    reply_to_id = db.Column(db.Integer, db.ForeignKey("comments.id"), nullable=True)

    author = db.relationship("User", backref="comments")
    reply_to = db.relationship("Comment", remote_side=[id], backref="replies")

