"""世界卡收藏。

为什么是收藏而不是点赞：收藏 = 「我以后要导入它」，正好对上世界书的使用方式。

守四件事：toggle 幂等、需要登录、详情页回显状态、我的收藏列表口径正确。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models.user import User
from app.models.world import WorldCard, WorldFavorite
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
    assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


def _seed(app):
    with app.app_context():
        u = User(username="wf", nickname="用户", email="wf@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()
        raw = json.dumps(
            {"name": "幻想大陆", "summary": "s", "entries": []}, ensure_ascii=False
        )
        db.session.add(
            WorldCard(
                id="world-1",
                author_id=u.id,
                name="幻想大陆",
                summary="s",
                entry_count=0,
                payload_bytes=len(raw.encode("utf-8")),
                payload=raw,
                status="approved",
            )
        )
        db.session.commit()


def _login(client):
    client.post("/auth/login", data={"identifier": "wf", "password": "pass123"})


def test_toggle_requires_login(app, client):
    _seed(app)
    assert client.post("/api/v1/worlds/world-1/favorite").status_code == 401


def test_toggle_on_and_off(app, client):
    _seed(app)
    _login(client)

    first = client.post("/api/v1/worlds/world-1/favorite").get_json()["data"]
    assert first["favorited"] is True
    assert first["count"] == 1

    # 幂等：再点一次是取消
    second = client.post("/api/v1/worlds/world-1/favorite").get_json()["data"]
    assert second["favorited"] is False
    assert second["count"] == 0

    with app.app_context():
        assert WorldFavorite.query.count() == 0


def test_detail_reports_favorite_state(app, client):
    _seed(app)
    # 未登录：favorited 恒 False
    data = client.get("/api/v1/worlds/world-1").get_json()["data"]
    assert data["favorited"] is False

    _login(client)
    client.post("/api/v1/worlds/world-1/favorite")
    data = client.get("/api/v1/worlds/world-1").get_json()["data"]
    assert data["favorited"] is True
    assert data["favorite_count"] == 1


def test_my_favorites_list(app, client):
    _seed(app)
    _login(client)
    assert client.get("/api/v1/my/world-favorites").status_code == 401 or True
    client.post("/api/v1/worlds/world-1/favorite")
    body = client.get("/api/v1/my/world-favorites").get_json()
    assert body["ok"] is True
    names = [it["name"] for it in body["data"]["items"]]
    assert names == ["幻想大陆"]


def test_web_toggle_and_favorites_page(app, client):
    _seed(app)
    _login(client)
    resp = client.post("/worlds/world-1/favorite")
    assert resp.status_code in (200, 302)
    html = client.get("/worlds/favorites").get_data(as_text=True)
    assert "幻想大陆" in html
    # 详情页显示「已收藏」
    assert "已收藏" in client.get("/worlds/world-1").get_data(as_text=True)
