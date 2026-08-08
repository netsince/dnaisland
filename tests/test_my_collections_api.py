import pytest
from app import create_app, db
from app.config import Config
from app.models.card import Card
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
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def test_my_favorites_and_likes_api(app, client):
    """GET /api/v1/my/favorites、/my/likes 返回收藏/点赞的角色卡列表。"""
    with app.app_context():
        author = User(
            username="mc_author", nickname="作者", email="mc_a@example.com",
        )
        author.set_password("pass123")
        u = User(username="mc_u", nickname="用户", email="mc_u@example.com")
        u.set_password("pass123")
        db.session.add_all([author, u])
        db.session.commit()
        card1 = Card(id="card-fav-1", author_id=author.id, name="收藏卡A", persona="P")
        card2 = Card(id="card-lk-1", author_id=author.id, name="点赞卡B", persona="P")
        # 普通用户只能看到 approved 的卡，收藏/点赞列表同样受可见性过滤。
        card1.status = "approved"
        card2.status = "approved"
        db.session.add_all([card1, card2])
        db.session.commit()

    # 未登录访问需要登录的接口 -> 401
    assert client.get("/api/v1/my/favorites").status_code == 401
    assert client.get("/api/v1/my/likes").status_code == 401

    client.post("/auth/login", data={"identifier": "mc_u", "password": "pass123"})

    # 收藏 card1、点赞 card2
    assert client.post("/api/v1/cards/card-fav-1/favorite").get_json()["ok"] is True
    assert client.post("/api/v1/cards/card-lk-1/like").get_json()["ok"] is True

    fav = client.get("/api/v1/my/favorites").get_json()
    assert fav["ok"] is True
    assert [c["id"] for c in fav["data"]["items"]] == ["card-fav-1"]

    lk = client.get("/api/v1/my/likes").get_json()
    assert lk["ok"] is True
    assert [c["id"] for c in lk["data"]["items"]] == ["card-lk-1"]

    # 取消收藏后不再出现在收藏列表
    assert client.post("/api/v1/cards/card-fav-1/favorite").get_json()["ok"] is True
    fav2 = client.get("/api/v1/my/favorites").get_json()
    assert fav2["data"]["items"] == []
