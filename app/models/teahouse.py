from ..extensions import db


class TeaPost(db.Model):
    """茶馆短帖（类 Twitter）。回复也是一条独立的帖子，通过 parent_id 形成层级。"""

    __tablename__ = "teahouse_posts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    parent_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_posts.id"), nullable=True, index=True
    )
    content = db.Column(db.Text, nullable=False)
    # 先发后审：is_hidden=被拒绝隐藏；moderated=是否已进入过审核流程
    is_hidden = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    moderated = db.Column(db.Boolean, server_default="0", nullable=False, index=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    author = db.relationship("User", backref="teaposts")
    # 父帖（被回复的那条）；replies 反向得到直接子回复
    parent = db.relationship(
        "TeaPost", remote_side=[id], backref="replies"
    )


class TeaPostLike(db.Model):
    __tablename__ = "teahouse_post_likes"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    post_id = db.Column(
        db.Integer, db.ForeignKey("teahouse_posts.id"), primary_key=True
    )
    created_at = db.Column(db.DateTime, server_default=db.func.now())
