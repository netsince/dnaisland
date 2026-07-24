from datetime import datetime, timedelta

from sqlalchemy.dialects.mysql import LONGTEXT

from ..extensions import db


# 发帖后允许编辑的时间窗口（分钟）
TEA_EDIT_WINDOW = timedelta(minutes=15)


class TeaPost(db.Model):
    """茶馆短帖（类 Twitter）。回复也是一条独立的帖子，通过 parent_id 形成层级。"""

    __tablename__ = "teahouse_posts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    parent_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_posts.id"), nullable=True, index=True
    )
    card_id = db.Column(
        db.String(36), db.ForeignKey("cards.id"), nullable=True, index=True
    )
    quote_post_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_posts.id"), nullable=True, index=True
    )
    content = db.Column(db.Text, nullable=False)
    # 先发后审：is_hidden=被拒绝隐藏；moderated=是否已进入过审核流程
    is_hidden = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    moderated = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    # 编辑 / 软删除（删除走软删，作者与管理员仍可看到）
    edited_at = db.Column(db.DateTime, nullable=True)
    is_deleted = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    deleted_at = db.Column(db.DateTime, nullable=True)

    author = db.relationship("User", backref="teaposts")

    def can_edit(self, user) -> bool:
        """当前用户能否编辑此帖：作者/超管、未隐藏未删、且在编辑窗口内。"""
        if not user or not getattr(user, "is_authenticated", False):
            return False
        if user.id != self.user_id and not user.is_super_admin:
            return False
        if self.is_deleted or self.is_hidden:
            return False
        if (datetime.utcnow() - self.created_at) > TEA_EDIT_WINDOW:
            return False
        return True
    # 父帖（被回复的那条）；replies 反向得到直接子回复
    parent = db.relationship(
        "TeaPost", remote_side=[id], foreign_keys=[parent_id], backref="replies"
    )
    card = db.relationship("Card", backref="teaposts")
    quote_post = db.relationship(
        "TeaPost", remote_side=[id], foreign_keys=[quote_post_id]
    )
    images = db.relationship(
        "TeaPostImage",
        backref="post",
        order_by="TeaPostImage.sort_order",
        cascade="all, delete-orphan",
    )
    poll = db.relationship(
        "TeaPoll",
        uselist=False,
        backref="post",
        cascade="all, delete-orphan",
    )


class TeaPostImage(db.Model):
    __tablename__ = "teahouse_post_images"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_posts.id"), nullable=False, index=True
    )
    image_data = db.Column(LONGTEXT, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class TeaTopic(db.Model):
    __tablename__ = "teahouse_topics"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False, index=True)
    post_count = db.Column(db.Integer, server_default="0", nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class TeaPostTopic(db.Model):
    __tablename__ = "teahouse_post_topics"

    post_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_posts.id"), primary_key=True
    )
    topic_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_topics.id"), primary_key=True
    )


class TeaPostLike(db.Model):
    __tablename__ = "teahouse_post_likes"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_posts.id"), primary_key=True
    )
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class TeaPostFavorite(db.Model):
    __tablename__ = "teahouse_post_favorites"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_posts.id"), primary_key=True
    )
    created_at = db.Column(db.DateTime, server_default=db.func.now())


class TeaPoll(db.Model):
    __tablename__ = "teahouse_polls"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_posts.id"), nullable=False, index=True
    )
    is_multiple = db.Column(db.Boolean, server_default="0", nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)

    options = db.relationship(
        "TeaPollOption", backref="poll", cascade="all, delete-orphan"
    )


class TeaPollOption(db.Model):
    __tablename__ = "teahouse_poll_options"

    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_polls.id"), nullable=False, index=True
    )
    option_text = db.Column(db.String(200), nullable=False)
    vote_count = db.Column(db.Integer, server_default="0", nullable=False)


class TeaPollVote(db.Model):
    __tablename__ = "teahouse_poll_votes"

    poll_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_polls.id"), primary_key=True
    )
    option_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_poll_options.id"), primary_key=True
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
