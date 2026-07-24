import pytest
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.mysql import LONGTEXT

from app import create_app, db
from app.config import Config
from app.models.card import Card, Comment
from app.models.notification import Notification
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


@pytest.fixture
def client(app):
    return app.test_client()


def test_comment_like_notification(app, client):
    """测试点赞他人评论生成通知，点赞自己评论不生成通知。"""
    with app.app_context():
        user1 = User(username="user1", nickname="用户一", email="u1@example.com")
        user1.set_password("pass123")
        user2 = User(username="user2", nickname="用户二", email="u2@example.com")
        user2.set_password("pass123")
        db.session.add_all([user1, user2])
        db.session.commit()
        user1_id = user1.id

        card = Card(id="card-1", author_id=user1.id, name="角色卡A", persona="Persona")
        db.session.add(card)
        db.session.commit()

        cm1 = Comment(card_id=card.id, user_id=user1.id, content="用户一的评论")
        db.session.add(cm1)
        db.session.commit()
        cm1_id = cm1.id

    # 1. user2 点赞 user1 的评论 -> 应给 user1 生成通知
    client.post("/auth/login", data={"identifier": "user2", "password": "pass123"})
    res = client.post(f"/card/card-1/comment/{cm1_id}/like")
    assert res.status_code == 200
    assert res.json["liked"] is True

    with app.app_context():
        n = Notification.query.filter_by(user_id=user1_id, type="comment_like").first()
        assert n is not None
        assert "用户二" in n.message
        assert "角色卡A" in n.message
        assert n.related_card_id == "card-1"

    # 2. user1 点赞自己 (user1) 的评论 -> 不应给 user1 生成新通知
    client.get("/auth/logout")
    client.post("/auth/login", data={"identifier": "user1", "password": "pass123"})

    with app.app_context():
        cm_self = Comment(card_id="card-1", user_id=user1_id, content="用户一自己的另一条评论")
        db.session.add(cm_self)
        db.session.commit()
        cm_self_id = cm_self.id
        count_before = Notification.query.filter_by(user_id=user1_id).count()

    res = client.post(f"/card/card-1/comment/{cm_self_id}/like")
    assert res.status_code == 200
    assert res.json["liked"] is True

    with app.app_context():
        count_after = Notification.query.filter_by(user_id=user1_id).count()
        assert count_after == count_before


def test_comment_reply_notification(app, client):
    """测试回复他人评论生成通知。"""
    with app.app_context():
        author = User(username="author", nickname="作者", email="author@example.com")
        author.set_password("pass123")
        u2 = User(username="user2", nickname="评论者A", email="u2@example.com")
        u2.set_password("pass123")
        u3 = User(username="user3", nickname="回复者B", email="u3@example.com")
        u3.set_password("pass123")
        db.session.add_all([author, u2, u3])
        db.session.commit()

        card = Card(id="card-2", author_id=author.id, name="角色卡B", persona="Persona")
        db.session.add(card)
        db.session.commit()

        # u2 发表主评论
        cm = Comment(card_id=card.id, user_id=u2.id, content="主评论")
        db.session.add(cm)
        db.session.commit()
        cm_id = cm.id
        u2_id = u2.id

    # u3 回复 u2 的评论 -> 应给 u2 生成 reply 通知
    client.post("/auth/login", data={"identifier": "user3", "password": "pass123"})
    res = client.post(
        f"/card/card-2/comment",
        data={"content": "这是回复内容", "reply_to_id": cm_id},
    )
    assert res.status_code in (200, 302)

    with app.app_context():
        n = Notification.query.filter_by(user_id=u2_id, type="comment_reply").first()
        assert n is not None
        assert "回复者B" in n.message
        assert "角色卡B" in n.message
        assert "这是回复内容" in n.message
        assert n.related_card_id == "card-2"


def test_card_comment_author_notification(app, client):
    """测试在他人的卡片下发表评论通知卡片作者。"""
    with app.app_context():
        author = User(username="card_author", nickname="卡片作者", email="ca@example.com")
        author.set_password("pass123")
        visitor = User(username="visitor", nickname="访客", email="v@example.com")
        visitor.set_password("pass123")
        db.session.add_all([author, visitor])
        db.session.commit()

        card = Card(id="card-3", author_id=author.id, name="角色卡C", persona="Persona")
        db.session.add(card)
        db.session.commit()
        author_id = author.id

    # 1. 访客在作者的卡片下发评论 -> 应通知卡片作者
    client.post("/auth/login", data={"identifier": "visitor", "password": "pass123"})
    res = client.post(
        "/card/card-3/comment",
        data={"content": "很好看的卡片！"},
    )
    assert res.status_code in (200, 302)

    with app.app_context():
        n = Notification.query.filter_by(user_id=author_id, type="card_comment").first()
        assert n is not None
        assert "访客" in n.message
        assert "角色卡C" in n.message
        assert n.related_card_id == "card-3"

    # 2. 作者在自己的卡片下发评论 -> 不应产生卡片评论通知
    client.get("/auth/logout")
    client.post("/auth/login", data={"identifier": "card_author", "password": "pass123"})
    res = client.post(
        "/card/card-3/comment",
        data={"content": "谢谢支持！"},
    )
    assert res.status_code in (200, 302)

    with app.app_context():
        author_card_comments = Notification.query.filter_by(
            user_id=author_id, type="card_comment"
        ).all()
        # 仍然只有之前访客发评论产生的那 1 条
        assert len(author_card_comments) == 1
