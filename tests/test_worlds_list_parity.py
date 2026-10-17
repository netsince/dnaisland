"""世界卡列表筛选的**双端一致性**。

P1 之前，/api/v1/worlds 与 /worlds/ 各自写了一份查询构造，网页那份**漏了 tag
筛选** —— 同一个筛选条件，两端行为不一样。现在共用
services/world_service.world_list_query，这条测试锁住"两端必须一致"，
防止以后又各自演化。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models.user import User
from app.models.world import WorldCard, WorldTag
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
    """两张已通过的世界卡：A 标签「奇幻」复制 5 次，B 标签「科幻」复制 1 次。"""
    with app.app_context():
        u = User(username="wl", nickname="作者", email="wl@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()

        for wid, name, tag, copies in (
            ("wa", "幻想大陆", "奇幻", 5),
            ("wb", "星海纪元", "科幻", 1),
        ):
            raw = json.dumps(
                {"name": name, "summary": name + "的简介", "entries": [], "tags": [tag]},
                ensure_ascii=False,
            )
            db.session.add(
                WorldCard(
                    id=wid,
                    author_id=u.id,
                    name=name,
                    summary=name + "的简介",
                    entry_count=0,
                    payload_bytes=len(raw.encode("utf-8")),
                    payload=raw,
                    status="approved",
                    copy_count=copies,
                )
            )
            db.session.add(WorldTag(world_id=wid, tag=tag))
        db.session.commit()


def _api_names(client, query=""):
    resp = client.get("/api/v1/worlds" + query)
    assert resp.status_code == 200
    return [it["name"] for it in resp.get_json()["data"]["items"]]


def _web_names(client, query=""):
    html = client.get("/worlds/" + query).get_data(as_text=True)
    return [n for n in ("幻想大陆", "星海纪元") if n in html]


def test_tag_filter_matches_on_both_ends(app, client):
    _seed(app)
    assert _api_names(client, "?tag=奇幻") == ["幻想大陆"]
    assert _web_names(client, "?tag=奇幻") == ["幻想大陆"]

    assert _api_names(client, "?tag=科幻") == ["星海纪元"]
    assert _web_names(client, "?tag=科幻") == ["星海纪元"]


def test_keyword_search_matches_on_both_ends(app, client):
    _seed(app)
    assert _api_names(client, "?q=星海") == ["星海纪元"]
    assert _web_names(client, "?q=星海") == ["星海纪元"]


def test_sort_copies_matches_on_both_ends(app, client):
    _seed(app)
    assert _api_names(client, "?sort=copies") == ["幻想大陆", "星海纪元"]
    # 网页版按同样顺序渲染：A 出现在 B 之前。
    html = client.get("/worlds/?sort=copies").get_data(as_text=True)
    assert html.index("幻想大陆") < html.index("星海纪元")
