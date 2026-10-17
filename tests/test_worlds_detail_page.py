"""世界卡详情页（网页版）：按「世界书」的形态渲染。

守三件事：
* 词条**按人物/名词分组**、可折叠、可搜索（世界书动辄几百条，平铺没法看）；
* 显示**浏览数**（后端一直在数，之前从不展示）；
* 有**举报入口**（target_type=world），但作者本人和未登录用户看不到。
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
        u = User(username="wd", nickname="作者", email="wd@example.com")
        u.set_password("pass123")
        other = User(username="viewer", nickname="路人", email="v@example.com")
        other.set_password("pass123")
        db.session.add_all([u, other])
        db.session.commit()
        raw = json.dumps(
            {
                "name": "幻想大陆",
                "summary": "一个世界",
                "entries": [
                    {"id": "e1", "name": "王都", "type": "noun", "keys": ["都城", "首都"]},
                    {"id": "e2", "name": "艾琳", "type": "person"},
                    {"id": "e3", "name": "魔法", "type": "noun"},
                ],
            },
            ensure_ascii=False,
        )
        db.session.add(
            WorldCard(
                id="world-1",
                author_id=u.id,
                name="幻想大陆",
                summary="一个世界",
                entry_count=3,
                payload_bytes=len(raw.encode("utf-8")),
                payload=raw,
                status="approved",
                view_count=0,
            )
        )
        db.session.commit()


def _html(client, path="/worlds/world-1"):
    resp = client.get(path)
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_entries_grouped_by_type(app, client):
    _seed(app)
    html = _html(client)
    assert "人物" in html and "名词 / 设定" in html
    for name in ("王都", "艾琳", "魔法"):
        assert name in html
    assert "都城" in html  # 触发词也展示


def test_entries_collapsible_and_filterable(app, client):
    _seed(app)
    html = _html(client)
    assert "<details" in html  # 原生折叠
    assert 'id="entryFilter"' in html
    assert "data-search=" in html


def test_view_count_is_displayed_and_increments(app, client):
    _seed(app)
    _html(client)
    html = _html(client)
    assert "次浏览" in html
    with app.app_context():
        assert db.session.get(WorldCard, "world-1").view_count == 2


def test_report_link_rules(app, client):
    _seed(app)
    # 未登录：没有举报入口（举报需要登录）
    assert "type=world" not in _html(client)
    # 作者本人：不举报自己
    client.post("/auth/login", data={"identifier": "wd", "password": "pass123"})
    assert "type=world" not in _html(client)
    # 别的登录用户：有举报入口
    client.get("/auth/logout")
    client.post("/auth/login", data={"identifier": "viewer", "password": "pass123"})
    assert "type=world" in _html(client)


def test_report_form_accepts_world(app, client):
    """举报表单页对 world 也要能打开（标题里显示「世界卡」）。"""
    _seed(app)
    client.post("/auth/login", data={"identifier": "viewer", "password": "pass123"})
    html = _html(client, "/report?type=world&id=world-1")
    assert "举报世界卡" in html
