"""世界卡搜索接入自带倒排索引。

与角色卡最大的不同：**世界卡的索引里包含词条名与触发词**。用户会按「王都」
这种词条名去找世界书，而不是记得世界书叫什么。

守三件事：发布时建索引、能按词条名搜到、索引不可用时回退 LIKE。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models.search_index import SearchDocStat
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


def _payload(name="幻想大陆", entries=None):
    return json.dumps(
        {
            "name": name,
            "summary": "一个测试世界",
            "entries": entries
            if entries is not None
            else [
                {"id": "e1", "name": "王都", "type": "noun", "keys": ["都城"]},
                {"id": "e2", "name": "艾琳", "type": "person"},
            ],
        },
        ensure_ascii=False,
    )


def _publish(app, client, name="幻想大陆"):
    with app.app_context():
        u = User(username="ws", nickname="作者", email="ws@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()
    client.post("/auth/login", data={"identifier": "ws", "password": "pass123"})
    resp = client.post(
        "/api/v1/worlds",
        data=_payload(name).encode("utf-8"),
        content_type="application/json",
    )
    world_id = resp.get_json()["data"]["id"]
    # 审核通过后对所有人可见
    with app.app_context():
        db.session.get(WorldCard, world_id).status = "approved"
        db.session.commit()
    return world_id


def _search(client, q):
    resp = client.get("/api/v1/worlds?q=" + q)
    return [it["name"] for it in resp.get_json()["data"]["items"]]


def test_publish_builds_index(app, client):
    _publish(app, client)
    with app.app_context():
        assert SearchDocStat.query.filter_by(doc_type="world").count() == 1


def test_search_by_entry_name(app, client):
    """核心：按**词条名**搜世界书 —— LIKE 只搜名称/简介时搜不到。"""
    _publish(app, client)
    assert _search(client, "王都") == ["幻想大陆"]


def test_search_by_trigger_word(app, client):
    _publish(app, client)
    assert _search(client, "都城") == ["幻想大陆"]


def test_search_by_world_name(app, client):
    _publish(app, client)
    assert _search(client, "幻想") == ["幻想大陆"]


def test_like_fallback_without_index(app, client):
    """没有索引时（老数据 / 索引未回填）仍要能按名称搜到。"""
    with app.app_context():
        u = User(username="ws2", nickname="作者2", email="ws2@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()
        raw = _payload("无索引世界")
        db.session.add(
            WorldCard(
                id="noidx",
                author_id=u.id,
                name="无索引世界",
                summary="一个测试世界",
                entry_count=0,
                payload_bytes=len(raw.encode("utf-8")),
                payload=raw,
                status="approved",
            )
        )
        db.session.commit()
        # 刻意不建索引
        assert SearchDocStat.query.filter_by(doc_type="world").count() == 0

    assert _search(client, "无索引") == ["无索引世界"]
