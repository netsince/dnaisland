"""「我发布的世界卡」接口（GET /api/v1/my/worlds）。

与 /my/world-favorites 的区别：这里**不套 visible_to** —— 作者要能看到自己的
待审核 / 已拒绝 / 已下架条目，客户端据此显示状态徽章。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models.user import User
from app.models.world import WorldCard
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
        u = User(username="mw", nickname="作者", email="mw@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()
        raw = json.dumps({"name": "幻想大陆", "summary": "s", "entries": []}, ensure_ascii=False)
        for wid, status, hidden in (
            ("w-pending", "pending", False),
            ("w-approved", "approved", False),
            ("w-hidden", "approved", True),
            ("w-rejected", "rejected", False),
        ):
            db.session.add(
                WorldCard(
                    id=wid,
                    author_id=u.id,
                    name="幻想大陆",
                    summary="s",
                    entry_count=0,
                    payload_bytes=len(raw.encode("utf-8")),
                    payload=raw,
                    status=status,
                    is_hidden=hidden,
                )
            )
        db.session.commit()


def _login(client):
    client.post("/auth/login", data={"identifier": "mw", "password": "pass123"})


def test_requires_login(app, client):
    assert client.get("/api/v1/my/worlds").status_code == 401


def test_lists_all_own_statuses(app, client):
    _seed(app)
    _login(client)
    body = client.get("/api/v1/my/worlds").get_json()
    assert body["ok"] is True
    statuses = sorted(it["status"] for it in body["data"]["items"])
    # 四条都在：pending / rejected / 两条 approved（含被下架的）
    assert statuses == ["approved", "approved", "pending", "rejected"]


def test_includes_hidden_ones(app, client):
    """作者要能看到自己被下架的条目（visible_to 会把它过滤掉）。"""
    _seed(app)
    _login(client)
    ids = [it["id"] for it in client.get("/api/v1/my/worlds").get_json()["data"]["items"]]
    assert "w-hidden" in ids
