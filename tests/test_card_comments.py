from datetime import UTC, datetime, timedelta, timezone

import pytest
from app import create_app, db
from app.config import Config
from app.models.card import Card, Comment
from app.models.user import User
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles


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
    assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"), \
        f"🧨 测试连到了非 SQLite 数据库！{app.config['SQLALCHEMY_DATABASE_URI']}"
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)



@pytest.fixture
def client(app):
    return app.test_client()


def test_comment_api_extended_fields(client, app):
    card_id = "test-card-1"
    with app.app_context():
        author = User(username="author", nickname="Author", email="author@example.com")
        author.set_password("password123")
        commenter = User(
            username="commenter", nickname="Commenter", email="commenter@example.com"
        )
        commenter.set_password("password123")
        db.session.add_all([author, commenter])
        db.session.commit()

        card = Card(
            id=card_id, author_id=author.id, name="Test Card", persona="Persona"
        )
        db.session.add(card)
        db.session.commit()

        now = datetime.now(UTC)
        cm1 = Comment(
            card_id=card_id,
            user_id=author.id,
            content="First comment by author",
            created_at=now - timedelta(minutes=5),
        )
        cm2 = Comment(
            card_id=card_id,
            user_id=commenter.id,
            content="Second comment by commenter",
            created_at=now,
        )
        db.session.add_all([cm1, cm2])
        db.session.commit()

    # 未登录访问 API
    res = client.get(f"/api/card/{card_id}/comments")
    assert res.status_code == 200
    data = res.get_json()
    assert "items" in data
    assert len(data["items"]) == 2

    item0 = data["items"][0]  # cm2 (最新，倒序第一条)
    item1 = data["items"][1]  # cm1 (最旧)

    assert "is_author" in item0
    assert "can_delete" in item0
    assert "delete_url" in item0
    assert "floor" in item0

    assert item0["is_author"] is False
    assert item0["floor"] == 2
    assert item0["can_delete"] is False

    assert item1["is_author"] is True
    assert item1["floor"] == 1
    assert item1["can_delete"] is False

    # 以 commenter 身份登录查看 API
    client.post(
        "/auth/login", data={"identifier": "commenter", "password": "password123"}
    )
    res = client.get(f"/api/card/{card_id}/comments")
    data = res.get_json()
    assert data["items"][0]["can_delete"] is True
    assert data["items"][1]["can_delete"] is False


def test_comment_length_limit(client, app):
    card_id = "test-card-2"
    with app.app_context():
        user = User(username="user1", nickname="User1", email="user1@example.com")
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()

        card = Card(
            id=card_id, author_id=user.id, name="Test Card 2", persona="Persona"
        )
        db.session.add(card)
        db.session.commit()

    client.post(
        "/auth/login", data={"identifier": "user1", "password": "password123"}
    )

    long_content = "a" * 501
    res = client.post(
        f"/card/{card_id}/comment",
        data={"content": long_content},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_comment_delete_permission(client, app):
    card_id = "test-card-3"
    with app.app_context():
        author = User(
            username="author2", nickname="Author2", email="author2@example.com"
        )
        author.set_password("password123")
        user2 = User(
            username="user2", nickname="User2", email="user2@example.com"
        )
        user2.set_password("password123")
        admin = User(
            username="admin",
            nickname="Admin",
            email="admin@example.com",
            role="super_admin",
        )
        admin.set_password("password123")
        db.session.add_all([author, user2, admin])
        db.session.commit()
        author_id = author.id

        card = Card(
            id=card_id, author_id=author_id, name="Test Card 3", persona="Persona"
        )
        db.session.add(card)
        db.session.commit()

        cm = Comment(card_id=card_id, user_id=author_id, content="Author comment")
        db.session.add(cm)
        db.session.commit()
        cm_id = cm.id

    # 1. 非本人 (user2) 尝试删除 -> 403
    client.post(
        "/auth/login", data={"identifier": "user2", "password": "password123"}
    )
    res = client.post(f"/card/{card_id}/comment/{cm_id}/delete")
    assert res.status_code == 403

    # 2. 本人 (author) 尝试删除 -> 200
    client.get("/auth/logout")
    client.post(
        "/auth/login", data={"identifier": "author2", "password": "password123"}
    )
    res = client.post(f"/card/{card_id}/comment/{cm_id}/delete")
    assert res.status_code == 200
    assert res.get_json().get("ok") is True

    # 3. 超级管理员 (admin) 尝试删除 -> 200
    with app.app_context():
        cm_admin_target = Comment(
            card_id=card_id, user_id=author_id, content="Another author comment"
        )
        db.session.add(cm_admin_target)
        db.session.commit()
        cm_admin_target_id = cm_admin_target.id

    client.get("/auth/logout")
    client.post(
        "/auth/login", data={"identifier": "admin", "password": "password123"}
    )
    res = client.post(f"/card/{card_id}/comment/{cm_admin_target_id}/delete")
    assert res.status_code == 200
    assert res.get_json().get("ok") is True
