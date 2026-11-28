"""角色卡撤回（软删除）：作者能删自己的卡，别人一律 404。

守四件事：
* 只有作者能撤回，别人的卡与不存在的卡都按 404 处理（不泄露存在性）；
* 是软删除 —— status 置 deleted 且 is_hidden=True，访客立刻看不到，
  作者「我的角色卡」里仍留着一条记录（作者要知道自己发过什么）；
* 撤回会释放置顶名额，否则名额被一张看不见的卡白占；
* 幂等：已经撤回的卡再撤一次不报错。
"""

from datetime import datetime

import pytest
from app import create_app, db
from app.config import Config
from app.models import Card
from app.models.user import User
from app.services.card_edit_service import (
    MAX_PINNED_CARDS,
    delete_card,
    set_card_pinned,
)
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


def _user(name):
    u = User(username=name, nickname=name, email=f"{name}@x.com")
    u.set_password("pw")
    db.session.add(u)
    db.session.commit()
    return u


def _card(author, name, status="approved", pinned_at=None):
    c = Card(
        id=f"card-{author.username}-{name}",
        author_id=author.id,
        name=name,
        gender="无性",
        persona="",
        status=status,
        pinned_at=pinned_at,
        created_at=datetime(2026, 1, 1),
    )
    db.session.add(c)
    db.session.commit()
    return c


def _login(client, name):
    client.post("/auth/login", data={"identifier": name, "password": "pw"})


# ---------------------------------------------------------------------------
# 服务层
# ---------------------------------------------------------------------------


def test_delete_marks_card_deleted_and_hidden(app):
    with app.app_context():
        u = _user("d1")
        c = _card(u, "a", pinned_at=datetime(2026, 2, 1))
        assert delete_card(u, c) is None
        row = db.session.get(Card, c.id)
        assert row.status == "deleted"
        assert row.is_hidden is True
        assert row.pinned_at is None


def test_delete_hides_from_visitors_but_keeps_author_row(app):
    with app.app_context():
        u = _user("d2")
        c = _card(u, "a")
        delete_card(u, c)
        # 访客看不到：visible_to 的基础过滤就是 is_hidden=False
        assert Card.visible_to(None).filter_by(id=c.id).first() is None
        # 作者自己的行还在（「我的角色卡」按 author_id 查，不过滤 status）
        assert Card.query.filter_by(author_id=u.id).count() == 1


def test_delete_rejects_other_author(app):
    with app.app_context():
        me, other = _user("d3"), _user("d3b")
        c = _card(other, "a")
        assert delete_card(me, c) == "无权操作此卡片"
        assert db.session.get(Card, c.id).status == "approved"


def test_delete_is_idempotent(app):
    with app.app_context():
        u = _user("d4")
        c = _card(u, "a")
        assert delete_card(u, c) is None
        assert delete_card(u, c) is None
        assert db.session.get(Card, c.id).status == "deleted"


def test_delete_frees_pin_slot(app):
    with app.app_context():
        u = _user("d5")
        c1, c2, c3 = _card(u, "a"), _card(u, "b"), _card(u, "c")
        for c in (c1, c2):
            assert set_card_pinned(u, c.id, True)[1] is None
        # 名额已满，第三张置不上
        assert set_card_pinned(u, c3.id, True)[1] is not None
        delete_card(u, c1)
        card, error = set_card_pinned(u, c3.id, True)
        assert error is None
        assert card.pinned_at is not None
        assert MAX_PINNED_CARDS == 2


# ---------------------------------------------------------------------------
# App 接口
# ---------------------------------------------------------------------------


def test_api_delete_own_card(app, client):
    with app.app_context():
        _user("a1")
        c = _card(User.query.filter_by(username="a1").first(), "a")
        card_id = c.id
    _login(client, "a1")
    resp = client.delete(f"/api/v1/cards/{card_id}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["data"]["status"] == "deleted"
    with app.app_context():
        assert db.session.get(Card, card_id).is_hidden is True


def test_api_delete_other_card_is_404(app, client):
    with app.app_context():
        _user("a2")
        other = _user("a2b")
        card_id = _card(other, "a").id
    _login(client, "a2")
    resp = client.delete(f"/api/v1/cards/{card_id}")
    assert resp.status_code == 404
    with app.app_context():
        assert db.session.get(Card, card_id).status == "approved"


def test_api_delete_missing_card_is_404(app, client):
    with app.app_context():
        _user("a3")
    _login(client, "a3")
    assert client.delete("/api/v1/cards/nope").status_code == 404


def test_api_delete_requires_login(app, client):
    with app.app_context():
        _user("a4")
        card_id = _card(User.query.filter_by(username="a4").first(), "a").id
    # 没有登录：api_login_required 拦在前面
    assert client.delete(f"/api/v1/cards/{card_id}").status_code == 401
