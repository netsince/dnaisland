"""世界卡：网页版页面 + 举报。

网页版这一期只做**浏览 + 下载**（发布仍在 App 侧），所以这里守的是：
* 列表页 / 详情页能渲染（模板别在线上炸）；
* 详情页只给词条名 + 一条示例，不给全文；
* 下载走的是同一份原文（逐字节相同）；
* 举报系统认 "world" 这个 target_type。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models.report import Report
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


def _world_json(name="幻想大陆"):
    return {
        "id": "local-1",
        "name": name,
        "summary": "一个用于测试的世界",
        "description": "",
        "tags": ["奇幻"],
        "forbiddenWords": [],
        "archived": False,
        "entries": [
            {"id": "e1", "name": "王都", "description": "首都", "type": "noun"},
            {"id": "e2", "name": "艾琳", "description": "女王", "type": "person"},
        ],
    }


def _seed(app, *, approved=True):
    with app.app_context():
        u = User(username="ww_user", nickname="作者", email="ww@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()
        raw = json.dumps(_world_json(), ensure_ascii=False)
        w = WorldCard(
            id="world-1",
            author_id=u.id,
            name="幻想大陆",
            summary="一个用于测试的世界",
            entry_count=2,
            payload_bytes=len(raw.encode("utf-8")),
            payload=raw,
            status="approved" if approved else "pending",
        )
        db.session.add(w)
        db.session.commit()
        return u.id, raw


def _login(client, username="ww_user"):
    client.post("/auth/login", data={"identifier": username, "password": "pass123"})


def test_home_page_links_to_worlds(app, client):
    """首页「换一换」左边有个「世界」入口 —— url_for 写错会让首页 500。"""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "/worlds/" in resp.get_data(as_text=True)


def test_profile_worlds_tab(app, client):
    """个人主页的「世界卡」tab —— 之前完全没有这个 tab。"""
    _seed(app)
    html = client.get("/user/ww_user?tab=worlds").get_data(as_text=True)
    assert "发布的世界卡" in html
    assert "幻想大陆" in html


def test_profile_worlds_tab_hides_pending_from_others(app, client):
    """别人看我的主页，待审核的世界卡不该出现。"""
    _seed(app, approved=False)
    html = client.get("/user/ww_user?tab=worlds").get_data(as_text=True)
    assert "幻想大陆" not in html


def test_profile_worlds_tab_shows_pending_to_owner(app, client):
    """本人看自己的主页，待审核的也要在（还带状态徽章）。"""
    _seed(app, approved=False)
    _login(client)
    html = client.get("/user/ww_user?tab=worlds").get_data(as_text=True)
    assert "幻想大陆" in html
    assert "待审核" in html


def test_list_page_renders(app, client):
    _seed(app)
    resp = client.get("/worlds/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "幻想大陆" in html
    # 列表页不展示正文。
    assert "王都" not in html


def test_detail_page_shows_names_and_one_sample(app, client):
    _seed(app)
    resp = client.get("/worlds/world-1")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "包含的词条" in html
    assert "王都" in html and "艾琳" in html
    assert "示例词条" in html
    # 只给一条示例，不是全部。
    assert html.count("示例词条") == 1


def test_detail_page_404_for_pending(app, client):
    _seed(app, approved=False)
    assert client.get("/worlds/world-1").status_code == 404


def test_download_returns_raw_text(app, client):
    _, raw = _seed(app)
    _login(client)
    resp = client.get("/worlds/world-1/download")
    assert resp.status_code == 200
    assert resp.data.decode("utf-8") == raw
    assert resp.headers["X-World-Copy-Count"] == "1"


def test_download_requires_login(app, client):
    _seed(app)
    resp = client.get("/worlds/world-1/download")
    assert resp.status_code in (302, 401)


def test_report_a_world(app, client):
    _seed(app)
    _login(client)
    resp = client.post(
        "/api/v1/reports",
        json={"type": "world", "id": "world-1", "reason": "spam"},
    )
    assert resp.get_json()["ok"] is True
    with app.app_context():
        report = Report.query.filter_by(target_type="world").first()
        assert report is not None
        assert report.target_id == "world-1"
