"""角色卡置顶：作者最多把 2 张「已通过」的卡置顶到主页最前。"""

from datetime import datetime

import pytest
from app import create_app, db
from app.config import Config
from app.models import Card
from app.models.user import User
from app.routes.card_lists import my_cards, profile_cards
from app.services.card_edit_service import MAX_PINNED_CARDS, set_card_pinned
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


def _card(author, name, status="approved", created=None):
    c = Card(
        id=f"card-{author.username}-{name}",
        author_id=author.id,
        name=name,
        gender="无性",
        persona="",
        status=status,
        created_at=created or datetime(2026, 1, 1),
    )
    db.session.add(c)
    db.session.commit()
    return c


# ---------------------------------------------------------------------------
# 服务层
# ---------------------------------------------------------------------------


def test_pin_approved_card(app):
    with app.app_context():
        u = _user("p1")
        c = _card(u, "a")
        card, error = set_card_pinned(u, c.id, True)
        assert error is None
        assert card.pinned_at is not None


def test_pin_rejects_non_approved(app):
    with app.app_context():
        u = _user("p2")
        c = _card(u, "a", status="pending")
        card, error = set_card_pinned(u, c.id, True)
        assert card is None
        assert error and "已通过" in error


def test_pin_limit_is_enforced(app):
    with app.app_context():
        u = _user("p3")
        c1, c2, c3 = _card(u, "a"), _card(u, "b"), _card(u, "c")
        assert set_card_pinned(u, c1.id, True)[1] is None
        assert set_card_pinned(u, c2.id, True)[1] is None
        card, error = set_card_pinned(u, c3.id, True)
        assert card is None
        assert error and str(MAX_PINNED_CARDS) in error
        # 第三张没有被置顶
        assert db.session.get(Card, c3.id).pinned_at is None


def test_pin_is_idempotent(app):
    with app.app_context():
        u = _user("p4")
        c = _card(u, "a")
        set_card_pinned(u, c.id, True)
        first = db.session.get(Card, c.id).pinned_at
        card, error = set_card_pinned(u, c.id, True)
        assert error is None
        assert card.pinned_at == first


def test_unpin(app):
    with app.app_context():
        u = _user("p5")
        c = _card(u, "a")
        set_card_pinned(u, c.id, True)
        card, error = set_card_pinned(u, c.id, False)
        assert error is None
        assert card.pinned_at is None


def test_pin_requires_ownership(app):
    with app.app_context():
        u1, u2 = _user("p6"), _user("p7")
        c = _card(u1, "a")
        card, error = set_card_pinned(u2, c.id, True)
        assert card is None
        assert error


# ---------------------------------------------------------------------------
# 排序：置顶优先
# ---------------------------------------------------------------------------


def test_profile_cards_pinned_first(app):
    with app.app_context():
        u = _user("p8")
        _card(u, "a", created=datetime(2026, 1, 3))
        b = _card(u, "b", created=datetime(2026, 1, 2))
        c = _card(u, "c", created=datetime(2026, 1, 1))
        _, _, cards = profile_cards(u, u.username, page=1, per_page=10)
        assert [x.name for x in cards] == ["a", "b", "c"]

        set_card_pinned(u, c.id, True)
        _, _, cards = profile_cards(u, u.username, page=1, per_page=10)
        assert [x.name for x in cards] == ["c", "a", "b"], "置顶卡应排在最前"

        set_card_pinned(u, b.id, True)
        _, _, cards = profile_cards(u, u.username, page=1, per_page=10)
        assert [x.name for x in cards] == ["b", "c", "a"], "两张置顶卡按置顶时间倒序"


def test_profile_cards_pinned_do_not_duplicate_across_pages(app):
    """置顶卡落在第一页，后续分页不重复出现。"""
    with app.app_context():
        u = _user("p9")
        a = _card(u, "a", created=datetime(2026, 1, 3))
        _card(u, "b", created=datetime(2026, 1, 2))
        _card(u, "c", created=datetime(2026, 1, 1))
        set_card_pinned(u, a.id, True)

        _, pag1, cards1 = profile_cards(u, u.username, page=1, per_page=2)
        _, pag2, cards2 = profile_cards(u, u.username, page=2, per_page=2)
        assert pag1.total == 3
        assert [x.name for x in cards1] == ["a", "b"]
        assert [x.name for x in cards2] == ["c"]


def test_my_cards_pinned_first(app):
    with app.app_context():
        u = _user("p10")
        _card(u, "a", created=datetime(2026, 1, 3))
        b = _card(u, "b", created=datetime(2026, 1, 2))
        set_card_pinned(u, b.id, True)
        _, cards = my_cards(u, page=1, per_page=10)
        assert [x.name for x in cards] == ["b", "a"]


# ---------------------------------------------------------------------------
# 接口层
# ---------------------------------------------------------------------------


def test_api_toggle_pin(app, client):
    with app.app_context():
        u = _user("api1")
        c = _card(u, "a")
        cid = c.id
    client.post("/auth/login", data={"identifier": "api1", "password": "pw"})

    r = client.post(f"/api/v1/cards/{cid}/toggle-pin")
    assert r.status_code == 200
    assert r.get_json()["data"]["pinned"] is True

    r = client.post(f"/api/v1/cards/{cid}/toggle-pin")
    assert r.status_code == 200
    assert r.get_json()["data"]["pinned"] is False


def test_api_toggle_pin_rejects_third(app, client):
    with app.app_context():
        u = _user("api2")
        ids = [_card(u, n).id for n in ("a", "b", "c")]
    client.post("/auth/login", data={"identifier": "api2", "password": "pw"})
    assert client.post(f"/api/v1/cards/{ids[0]}/toggle-pin").status_code == 200
    assert client.post(f"/api/v1/cards/{ids[1]}/toggle-pin").status_code == 200
    r = client.post(f"/api/v1/cards/{ids[2]}/toggle-pin")
    assert r.status_code == 400
    assert str(MAX_PINNED_CARDS) in r.get_json()["error"]


def test_profile_page_shows_pinned_badge(app, client):
    with app.app_context():
        u = _user("web1")
        c = _card(u, "a")
        set_card_pinned(u, c.id, True)
    r = client.get("/user/web1")
    assert r.status_code == 200
    assert "dna-tile__pin" in r.get_data(as_text=True)


def test_my_cards_page_only_offers_pin_for_approved(app, client):
    """未通过的卡不应出现置顶按钮（服务端也会拒绝，这里避免误导）。"""
    with app.app_context():
        u = _user("web2")
        _card(u, "approved_one", status="approved")
        _card(u, "pending_one", status="pending")
    client.post("/auth/login", data={"identifier": "web2", "password": "pw"})
    html = client.get("/my/cards").get_data(as_text=True)
    # 只有 1 张已通过的卡 → 恰好 1 个置顶表单
    assert html.count("toggle-pin") == 1


def test_api_card_list_exposes_pinned(app, client):
    with app.app_context():
        u = _user("api3")
        c = _card(u, "a")
        set_card_pinned(u, c.id, True)
    client.post("/auth/login", data={"identifier": "api3", "password": "pw"})
    r = client.get("/api/v1/users/api3")
    assert r.status_code == 200
    item = r.get_json()["data"]["cards"]["items"][0]
    assert item["pinned"] is True


def test_api_explore_does_not_expose_pinned(app, client):
    """置顶角标只属于「作者自己的列表」。

    用户反馈：首页/探索里别人置顶的卡也挂着「置顶」角标。pinned 字段现在只在
    作者主页与「我的角色卡」里为 true，探索（sort=new 是确定序）必须为 false。
    """
    with app.app_context():
        u = _user("api5")
        c = _card(u, "explore_me")
        set_card_pinned(u, c.id, True)
        cid = c.id
    client.post("/auth/login", data={"identifier": "api5", "password": "pw"})

    r = client.get("/api/v1/cards/explore?sort=new")
    assert r.status_code == 200
    items = r.get_json()["data"]["items"]
    mine = [it for it in items if it["id"] == cid]
    assert mine, "这张卡应该在探索结果里（否则用例是空跑）"
    assert mine[0]["pinned"] is False


def test_web_search_does_not_show_pinned_badge(app, client):
    """网页版同理：搜索/首页这类列表不该出现「置顶」角标。"""
    with app.app_context():
        u = _user("web3")
        c = _card(u, "searchable_pin")
        set_card_pinned(u, c.id, True)

    html = client.get("/search?q=searchable_pin").get_data(as_text=True)
    assert "searchable_pin" in html, "卡片应该出现在搜索结果里（否则用例是空跑）"
    assert "dna-tile__pin" not in html
