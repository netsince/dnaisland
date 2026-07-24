import pytest
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.mysql import LONGTEXT

from app import create_app, db
from app.config import Config
from app.models.card import Card, Comment, CommentLike
from app.models.user import User


@compiles(LONGTEXT, "sqlite")
def compile_longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    app = create_app(TestConfig)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_comment_like_model(app):
    """测试 CommentLike 点赞模型的添加与查询"""
    with app.app_context():
        user = User(username="liker", nickname="Liker", email="liker@example.com")
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()

        card = Card(id="card-like-1", author_id=user.id, name="Test Card", persona="Persona")
        db.session.add(card)
        db.session.commit()

        comment = Comment(card_id=card.id, user_id=user.id, content="Great card!")
        db.session.add(comment)
        db.session.commit()

        # 添加点赞
        like = CommentLike(user_id=user.id, comment_id=comment.id)
        db.session.add(like)
        db.session.commit()

        # 查询点赞
        fetched_like = CommentLike.query.filter_by(user_id=user.id, comment_id=comment.id).first()
        assert fetched_like is not None
        assert fetched_like.created_at is not None

        # 统计某个评论的点赞数
        likes_count = CommentLike.query.filter_by(comment_id=comment.id).count()
        assert likes_count == 1


def test_comment_is_pinned_attribute(app):
    """测试 Comment 的 is_pinned 属性"""
    with app.app_context():
        user = User(username="user_pin", nickname="PinUser", email="pin@example.com")
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()

        card = Card(id="card-pin-1", author_id=user.id, name="Pin Card", persona="Persona")
        db.session.add(card)
        db.session.commit()

        comment = Comment(card_id=card.id, user_id=user.id, content="Normal comment")
        db.session.add(comment)
        db.session.commit()

        assert comment.is_pinned is False

        # 修改为置顶
        comment.is_pinned = True
        db.session.commit()

        fetched_comment = db.session.get(Comment, comment.id)
        assert fetched_comment.is_pinned is True


def test_comment_reply_to_relationship(app):
    """测试 Comment 的 reply_to_id / reply_to 自关联关系"""
    with app.app_context():
        user1 = User(username="user_reply1", nickname="User1", email="u1@example.com")
        user1.set_password("password123")
        user2 = User(username="user_reply2", nickname="User2", email="u2@example.com")
        user2.set_password("password123")
        db.session.add_all([user1, user2])
        db.session.commit()

        card = Card(id="card-reply-1", author_id=user1.id, name="Reply Card", persona="Persona")
        db.session.add(card)
        db.session.commit()

        # 父评论
        parent_comment = Comment(card_id=card.id, user_id=user1.id, content="Parent comment")
        db.session.add(parent_comment)
        db.session.commit()

        # 子评论（回复父评论）
        child_comment = Comment(
            card_id=card.id,
            user_id=user2.id,
            content="Child reply comment",
            reply_to_id=parent_comment.id
        )
        db.session.add(child_comment)
        db.session.commit()

        # 校验 reply_to 关系
        fetched_child = db.session.get(Comment, child_comment.id)
        assert fetched_child.reply_to_id == parent_comment.id
        assert fetched_child.reply_to is not None
        assert fetched_child.reply_to.id == parent_comment.id
        assert fetched_child.reply_to.content == "Parent comment"

        # 校验 backref replies 关系
        fetched_parent = db.session.get(Comment, parent_comment.id)
        assert len(fetched_parent.replies) == 1
        assert fetched_parent.replies[0].id == child_comment.id
        assert fetched_parent.replies[0].content == "Child reply comment"
