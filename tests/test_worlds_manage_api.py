"""世界卡的作者管理接口：编辑 / 下架 / 重新提审 / 取原文。

守五件事：
* 只有作者能编辑、下架、重新提审、取原文（别人一律 404）；
* 编辑是**整份替换**，并且必须回到 pending —— 审核过的是旧内容；
* 编辑后词条名快照与标签一起更新（否则卡片上还是旧名字）；
* 取原文用于编辑，**不记复制统计**（作者改自己的东西不算别人复制了一次）；
* 重新提审只对 rejected / deleted 有意义。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models.user import User
from app.models.world import WorldCard, WorldCopyStat, WorldTag
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


def _payload(name="幻想大陆", entries=None, tags=None):
    return json.dumps(
        {
            "id": "local-1",
            "name": name,
            "summary": name + "的简介",
            "description": "",
            "tags": tags if tags is not None else ["奇幻"],
            "forbiddenWords": [],
            "archived": False,
            "entries": entries
            if entries is not None
            else [
                {"id": "e1", "name": "王都", "description": "首都", "type": "noun"},
                {"id": "e2", "name": "艾琳", "description": "女王", "type": "person"},
            ],
        },
        ensure_ascii=False,
    )


def _seed(app, *, status="approved"):
    """作者 me + 路人 other + 一个世界卡。返回 world_id。"""
    with app.app_context():
        me = User(username="me", nickname="作者", email="me@example.com")
        me.set_password("pass123")
        other = User(username="other", nickname="路人", email="o@example.com")
        other.set_password("pass123")
        db.session.add_all([me, other])
        db.session.commit()

        raw = _payload()
        world = WorldCard(
            id="world-1",
            author_id=me.id,
            name="幻想大陆",
            summary="幻想大陆的简介",
            entry_count=2,
            payload_bytes=len(raw.encode("utf-8")),
            payload=raw,
            preview_names=json.dumps(["王都", "艾琳"], ensure_ascii=False),
            status=status,
        )
        db.session.add(world)
        db.session.add(WorldTag(world_id="world-1", tag="奇幻"))
        db.session.commit()
        return "world-1"


def _login(client, username="me"):
    client.post("/auth/login", data={"identifier": username, "password": "pass123"})


# ---------------------------------------------------------------------------
# 编辑
# ---------------------------------------------------------------------------


def test_edit_replaces_payload_and_requeues(app, client):
    _seed(app)
    _login(client)
    new_raw = _payload(
        name="新世界",
        entries=[{"id": "e9", "name": "新词条", "description": "新正文", "type": "person"}],
        tags=["科幻"],
    )
    resp = client.put(
        "/api/v1/worlds/world-1",
        data=new_raw,
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["data"]["status"] == "pending"

    with app.app_context():
        world = db.session.get(WorldCard, "world-1")
        assert world.name == "新世界"
        assert world.status == "pending"
        assert world.entry_count == 1
        # 卡片上的词条名快照也要跟着更新
        assert world.preview_list == ["新词条"]
        assert [t.tag for t in WorldTag.query.filter_by(world_id="world-1")] == ["科幻"]
        assert json.loads(world.payload)["name"] == "新世界"


def test_edit_requires_ownership(app, client):
    _seed(app)
    _login(client, "other")
    resp = client.put(
        "/api/v1/worlds/world-1",
        data=_payload(name="篡改"),
        content_type="application/json",
    )
    assert resp.status_code == 404
    with app.app_context():
        assert db.session.get(WorldCard, "world-1").name == "幻想大陆"


def test_edit_rejects_bad_json(app, client):
    _seed(app)
    _login(client)
    resp = client.put("/api/v1/worlds/world-1", data="not json", content_type="application/json")
    assert resp.status_code == 400
    with app.app_context():
        assert db.session.get(WorldCard, "world-1").status == "approved"


# ---------------------------------------------------------------------------
# 下架 / 重新提审
# ---------------------------------------------------------------------------


def test_hide_toggles(app, client):
    _seed(app)
    _login(client)
    body = client.post("/api/v1/worlds/world-1/hide").get_json()
    assert body["data"]["hidden"] is True
    with app.app_context():
        assert db.session.get(WorldCard, "world-1").is_hidden is True

    body = client.post("/api/v1/worlds/world-1/hide").get_json()
    assert body["data"]["hidden"] is False


def test_hide_requires_ownership(app, client):
    _seed(app)
    _login(client, "other")
    assert client.post("/api/v1/worlds/world-1/hide").status_code == 404


def test_resubmit_only_for_rejected_or_deleted(app, client):
    _seed(app, status="rejected")
    _login(client)
    body = client.post("/api/v1/worlds/world-1/resubmit").get_json()
    assert body["data"]["status"] == "pending"
    with app.app_context():
        assert db.session.get(WorldCard, "world-1").status == "pending"


def test_resubmit_rejects_approved(app, client):
    _seed(app, status="approved")
    _login(client)
    assert client.post("/api/v1/worlds/world-1/resubmit").status_code == 400


# ---------------------------------------------------------------------------
# 取原文（编辑用，不记复制）
# ---------------------------------------------------------------------------


def test_source_is_author_only_and_not_counted_as_copy(app, client):
    _seed(app)
    _login(client)
    resp = client.get("/api/v1/worlds/world-1/source")
    assert resp.status_code == 200
    assert json.loads(resp.get_data(as_text=True))["name"] == "幻想大陆"

    with app.app_context():
        world = db.session.get(WorldCard, "world-1")
        # 作者改自己的东西，不该被算成别人复制了一次
        assert (world.copy_count or 0) == 0
        assert WorldCopyStat.query.count() == 0


def test_source_requires_ownership(app, client):
    _seed(app)
    _login(client, "other")
    assert client.get("/api/v1/worlds/world-1/source").status_code == 404


# ---------------------------------------------------------------------------
# 网页版：我的世界卡 / 下架 / 重新提审
# ---------------------------------------------------------------------------


def test_web_mine_lists_own_worlds_with_status(app, client):
    _seed(app, status="pending")
    _login(client)
    html = client.get("/worlds/mine").get_data(as_text=True)
    assert "我的世界卡" in html
    assert "幻想大陆" in html
    # 作者要能看出状态
    assert "待审核" in html


def test_web_mine_requires_login(app, client):
    _seed(app)
    assert client.get("/worlds/mine").status_code in (302, 401)


def test_web_hide_and_resubmit(app, client):
    _seed(app, status="rejected")
    _login(client)

    client.post("/worlds/world-1/hide")
    with app.app_context():
        assert db.session.get(WorldCard, "world-1").is_hidden is True

    client.post("/worlds/world-1/resubmit")
    with app.app_context():
        world = db.session.get(WorldCard, "world-1")
        assert world.status == "pending"
        assert world.is_hidden is False
