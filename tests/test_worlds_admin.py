"""后台的世界卡审核与管理。

世界卡审核**并进了角色卡审核中心**（/admin/review?type=world），不再是独立一页；
/admin/worlds 只保留一个跳转，老书签不会 404。

守四件事：
* 审核权限：非审核员进不去；
* 通过/驳回**必须通知作者**，且用通用关联 related_type="world"（客户端靠它跳转）；
* 批量操作与角色卡共用 /admin/review/batch，靠 type 区分；
* 下架/删除都是软操作，且对公开接口立即可见地生效。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models.notification import Notification
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


def _payload(name="幻想大陆"):
    return json.dumps(
        {
            "id": "local-1",
            "name": name,
            "summary": "测试世界",
            "description": "",
            "tags": ["奇幻"],
            "forbiddenWords": [],
            "archived": False,
            "entries": [{"id": "e1", "name": "王都", "description": "首都", "type": "noun"}],
        },
        ensure_ascii=False,
    )


def _seed(app, *, status="pending", name="幻想大陆"):
    """建一个作者 + 一个审核员 + 一个世界卡。返回 (world_id, author_id)。"""
    with app.app_context():
        author = User(username="wa", nickname="作者", email="wa@example.com")
        author.set_password("pass123")
        admin = User(
            username="wadmin", nickname="审核员", email="wadmin@example.com", role="super_admin"
        )
        admin.set_password("pass123")
        db.session.add_all([author, admin])
        db.session.commit()

        raw = _payload(name)
        world = WorldCard(
            id="world-1",
            author_id=author.id,
            name=name,
            summary="测试世界",
            entry_count=1,
            payload_bytes=len(raw.encode("utf-8")),
            payload=raw,
            status=status,
        )
        db.session.add(world)
        db.session.commit()
        return world.id, author.id


def _login(client, username="wadmin"):
    client.post("/auth/login", data={"identifier": username, "password": "pass123"})


def test_requires_review_permission(app, client):
    _seed(app)
    resp = client.get("/admin/worlds")
    # 未登录 → 跳登录页
    assert resp.status_code in (302, 401, 403)


def test_old_entry_redirects_to_review(app, client):
    """旧入口 /admin/worlds 跳到审核中心的世界卡队列。"""
    _seed(app)
    _login(client)
    resp = client.get("/admin/worlds")
    assert resp.status_code == 302
    assert "type=world" in resp.headers["Location"]


def test_list_renders_pending(app, client):
    _seed(app)
    _login(client)
    resp = client.get("/admin/review?type=world&status=pending")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "幻想大陆" in html
    # 与角色卡审核同一个页面：类型切换 + 同一张表
    assert "世界卡审核队列" in html
    assert "角色卡" in html


def test_reviewer_can_open_pending_world(app, client):
    """审核员点「详情」要能打开待审核的世界卡，不能 404。

    审核中心用的是 /worlds/<id> 做预览，而那条路由走 can_view —— 早先只放
    超管，审核员会点出一个 404。
    """
    world_id, _ = _seed(app)
    _login(client)
    with app.app_context():
        # 把当前审核员降成普通审核员（不是超管）
        admin = User.query.filter_by(username="wadmin").first()
        admin.role = "reviewer"
        db.session.commit()

    assert client.get(f"/worlds/{world_id}").status_code == 200


def test_card_queue_still_works(app, client):
    """合并不是把角色卡审核挤掉：type 缺省仍然是角色卡队列。"""
    _seed(app)
    _login(client)
    resp = client.get("/admin/review")
    assert resp.status_code == 200
    assert "角色卡审核队列" in resp.get_data(as_text=True)


def test_approve_publishes_and_notifies(app, client):
    world_id, author_id = _seed(app)
    _login(client)
    resp = client.post(f"/admin/worlds/{world_id}/approve")
    assert resp.status_code in (200, 302)

    with app.app_context():
        world = db.session.get(WorldCard, world_id)
        assert world.status == "approved"
        note = Notification.query.filter_by(user_id=author_id, type="review").first()
        assert note is not None
        assert note.related_type == "world"
        assert note.related_id == world_id
        assert "已通过审核" in note.message


def test_reject_with_reason_notifies(app, client):
    world_id, author_id = _seed(app)
    _login(client)
    client.post(f"/admin/worlds/{world_id}/reject", data={"reason": "内容与简介不符"})

    with app.app_context():
        world = db.session.get(WorldCard, world_id)
        assert world.status == "rejected"
        note = Notification.query.filter_by(user_id=author_id, type="review").first()
        assert note is not None
        assert "内容与简介不符" in note.message


def test_hide_toggles_and_hides_from_public(app, client):
    world_id, _ = _seed(app, status="approved")
    _login(client)
    client.post(f"/admin/worlds/{world_id}/hide")
    with app.app_context():
        assert db.session.get(WorldCard, world_id).is_hidden is True

    client.get("/auth/logout")
    assert client.get(f"/worlds/{world_id}").status_code == 404

    _login(client)
    client.post(f"/admin/worlds/{world_id}/hide")
    with app.app_context():
        assert db.session.get(WorldCard, world_id).is_hidden is False


def test_delete_is_soft(app, client):
    world_id, _ = _seed(app)
    _login(client)
    client.post(f"/admin/worlds/{world_id}/delete")
    with app.app_context():
        world = db.session.get(WorldCard, world_id)
        assert world.status == "deleted"
        assert world.is_hidden is True
        # 行还在（world_copy_stats 有外键指向它，硬删会丢版权溯源）
        assert world is not None


def test_batch_approve(app, client):
    """批量通过走审核中心的共用入口，用 type=world 区分对象。"""
    world_id, author_id = _seed(app)
    _login(client)
    client.post(
        "/admin/review/batch",
        data={"type": "world", "action": "approve", "card_ids": [world_id]},
    )
    with app.app_context():
        assert db.session.get(WorldCard, world_id).status == "approved"
        assert (
            Notification.query.filter_by(user_id=author_id, type="review").count() == 1
        )


def test_batch_reject_with_reason(app, client):
    world_id, author_id = _seed(app)
    _login(client)
    client.post(
        "/admin/review/batch",
        data={
            "type": "world",
            "action": "reject",
            "card_ids": [world_id],
            "reason": "词条太少",
        },
    )
    with app.app_context():
        assert db.session.get(WorldCard, world_id).status == "rejected"
        note = Notification.query.filter_by(user_id=author_id, type="review").first()
        assert note is not None and "词条太少" in note.message
