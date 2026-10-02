"""角色卡隐匿标签：仅管理员可见，可驱动「减少推流」降权。

关键约束（必须回归）：普通用户/作者**任何接口都拿不到** hidden_tags / boost_factor。
"""

from datetime import datetime

import pytest
from app import create_app, db
from app.config import Config
from app.models import Card
from app.models.user import User
from app.routes.main import _apply_hot_score
from app.services.card_hidden_tags import (
    HIDDEN_TAGS,
    REDUCE_BOOST,
    boost_factor_for,
    hidden_tag_views,
    normalize_hidden_tags,
    set_hidden_tags,
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


def _user(name, role="user"):
    u = User(username=name, nickname=name, email=f"{name}@x.com", password_hash="x", role=role)
    db.session.add(u)
    db.session.commit()
    return u


def _card(author, name, created=None):
    c = Card(
        id=f"card-{name}",
        author_id=author.id,
        name=name,
        gender="无性",
        persona="",
        status="approved",
        created_at=created or datetime.now(),
    )
    db.session.add(c)
    db.session.commit()
    return c


# ---------------------------------------------------------------------------
# 注册表与规范化
# ---------------------------------------------------------------------------


def test_registry_has_reduce_boost():
    assert REDUCE_BOOST in HIDDEN_TAGS
    assert HIDDEN_TAGS[REDUCE_BOOST]["label"] == "减少推流"
    assert HIDDEN_TAGS[REDUCE_BOOST]["boost"] == pytest.approx(0.2)


def test_normalize_filters_unknown_and_dedupes():
    assert normalize_hidden_tags([REDUCE_BOOST, REDUCE_BOOST]) == [REDUCE_BOOST]
    assert normalize_hidden_tags(["不存在的标签", REDUCE_BOOST]) == [REDUCE_BOOST]
    assert normalize_hidden_tags(None) == []
    assert normalize_hidden_tags("reduce_boost") == [REDUCE_BOOST]  # 容忍单个字符串


def test_boost_factor():
    assert boost_factor_for([]) == 1.0
    assert boost_factor_for([REDUCE_BOOST]) == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# 写入：JSON 与 SQL 可见系数必须一致
# ---------------------------------------------------------------------------


def test_set_hidden_tags_keeps_json_and_factor_in_sync(app):
    with app.app_context():
        u = _user("a1")
        c = _card(u, "x")

        set_hidden_tags(c, [REDUCE_BOOST])
        assert c.hidden_tags == [REDUCE_BOOST]
        assert float(c.boost_factor) == pytest.approx(0.2)

        set_hidden_tags(c, [])
        assert c.hidden_tags == []
        assert float(c.boost_factor) == pytest.approx(1.0)


def test_set_hidden_tags_ignores_unknown_keys(app):
    with app.app_context():
        u = _user("a2")
        c = _card(u, "y")
        set_hidden_tags(c, ["乱写的", REDUCE_BOOST])
        assert c.hidden_tags == [REDUCE_BOOST]


def test_hidden_tag_views_expose_label_for_admin(app):
    with app.app_context():
        u = _user("a3")
        c = _card(u, "z")
        set_hidden_tags(c, [REDUCE_BOOST])
        views = hidden_tag_views(c)
        assert views and views[0]["label"] == "减少推流"


# ---------------------------------------------------------------------------
# 降权：热度分 ×0.2
# ---------------------------------------------------------------------------


def test_reduce_boost_scales_hot_score(app):
    with app.app_context():
        u = _user("a4")
        now = datetime.now()
        plain = _card(u, "plain", created=now)
        flagged = _card(u, "flagged", created=now)
        # 给相同的浏览量，让分数非零且两卡只有降权系数不同。
        for c in (plain, flagged):
            c.view_count = 100
        set_hidden_tags(flagged, [REDUCE_BOOST])
        db.session.commit()

        q, expr = _apply_hot_score(Card.visible_to(None))
        scores = {cid: float(s or 0.0) for cid, s in q.with_entities(Card.id, expr).all()}
        assert scores[plain.id] > 0
        assert scores[flagged.id] == pytest.approx(scores[plain.id] * 0.2, rel=0.01)


# ---------------------------------------------------------------------------
# 不可见性（硬约束）
# ---------------------------------------------------------------------------


def test_api_card_payload_never_exposes_hidden_tags(app, client):
    with app.app_context():
        u = _user("a5")
        c = _card(u, "secret")
        set_hidden_tags(c, [REDUCE_BOOST])
        cid = c.id

    # 详情（匿名可见：approved 未隐藏）
    body = client.get(f"/api/v1/cards/{cid}").get_data(as_text=True)
    assert "hidden_tags" not in body
    assert "boost_factor" not in body
    assert REDUCE_BOOST not in body

    # 首页推荐列表
    body = client.get("/api/v1/cards/featured").get_data(as_text=True)
    assert "hidden_tags" not in body
    assert "boost_factor" not in body


def test_web_card_detail_never_exposes_hidden_tags(app, client):
    with app.app_context():
        u = _user("a6")
        c = _card(u, "secret2")
        set_hidden_tags(c, [REDUCE_BOOST])
        cid = c.id
    html = client.get(f"/card/{cid}").get_data(as_text=True)
    assert "hidden_tags" not in html
    assert "boost_factor" not in html
    assert "减少推流" not in html


# ---------------------------------------------------------------------------
# 管理后台
# ---------------------------------------------------------------------------


def test_admin_can_set_and_clear_hidden_tag(app, client):
    with app.app_context():
        admin = _user("boss", role="super_admin")
        admin.set_password("pw")  # 只有这一个用户需要真密码，bcrypt 开销可接受
        author = _user("a7")
        c = _card(author, "admin-target")
        cid = c.id
        db.session.commit()

    client.post("/auth/login", data={"identifier": "boss", "password": "pw"})
    r = client.post(
        f"/admin/cards/{cid}/edit",
        data={"name": "admin-target", "status": "approved", "hidden_tags": [REDUCE_BOOST]},
        follow_redirects=False,
    )
    assert r.status_code in (302, 200)
    with app.app_context():
        assert db.session.get(Card, cid).hidden_tags == [REDUCE_BOOST]

    # 取消勾选后应清空
    client.post(
        f"/admin/cards/{cid}/edit",
        data={"name": "admin-target", "status": "approved"},
        follow_redirects=False,
    )
    with app.app_context():
        card = db.session.get(Card, cid)
        assert card.hidden_tags == []
        assert float(card.boost_factor) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 三处设置入口 + 后台筛选
# ---------------------------------------------------------------------------


def _admin_and_card(admin_name, card_name):
    admin = _user(admin_name, role="super_admin")
    admin.set_password("pw")
    author = _user(f"{admin_name}_author")
    author.set_password("pw")
    c = _card(author, card_name)
    db.session.commit()
    return admin, author, c


def test_admin_cards_filter_by_hidden_tag(app, client):
    with app.app_context():
        _admin, _author, flagged = _admin_and_card("bossf", "ZZFLAGGED")
        _card(_author, "ZZPLAIN")
        set_hidden_tags(flagged, [REDUCE_BOOST])
        db.session.commit()
        flagged_id = flagged.id

    client.post("/auth/login", data={"identifier": "bossf", "password": "pw"})
    html = client.get(f"/admin/cards?hidden_tag={REDUCE_BOOST}").get_data(as_text=True)
    assert "ZZFLAGGED" in html
    assert "ZZPLAIN" not in html

    # 不筛选时两张都在
    html = client.get("/admin/cards").get_data(as_text=True)
    assert "ZZFLAGGED" in html and "ZZPLAIN" in html
    assert flagged_id  # 仅用于避免未使用告警


def test_review_page_renders_hidden_tag_form(app, client):
    with app.app_context():
        _admin, _author, c = _admin_and_card("bossr", "ZZREVIEW")
        cid = c.id
    client.post("/auth/login", data={"identifier": "bossr", "password": "pw"})
    html = client.get(f"/admin/review/{cid}").get_data(as_text=True)
    assert "hidden_tags" in html
    assert f"/admin/cards/{cid}/hidden-tags" in html


def test_shared_endpoint_sets_tags_and_redirects_back(app, client):
    with app.app_context():
        _admin, _author, c = _admin_and_card("bosss", "ZZSHARED")
        cid = c.id
    client.post("/auth/login", data={"identifier": "bosss", "password": "pw"})

    r = client.post(
        f"/admin/cards/{cid}/hidden-tags",
        data={"hidden_tags": [REDUCE_BOOST], "next": f"/admin/review/{cid}"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith(f"/admin/review/{cid}")
    with app.app_context():
        assert db.session.get(Card, cid).hidden_tags == [REDUCE_BOOST]


def test_shared_endpoint_rejects_external_next(app, client):
    """防开放重定向：next 只允许站内路径。"""
    with app.app_context():
        _admin, _author, c = _admin_and_card("bossx", "ZZREDIR")
        cid = c.id
    client.post("/auth/login", data={"identifier": "bossx", "password": "pw"})
    r = client.post(
        f"/admin/cards/{cid}/hidden-tags",
        data={"hidden_tags": [], "next": "https://evil.example.com/x"},
    )
    assert "evil.example.com" not in r.headers["Location"]


def test_frontend_panel_visible_only_to_admin(app, client):
    """前台详情页的隐匿标签面板：匿名与作者本人都看不到，仅超管可见。"""
    with app.app_context():
        _admin, author, c = _admin_and_card("bossp", "ZZPANEL")
        cid = c.id

    html = client.get(f"/card/{cid}").get_data(as_text=True)
    assert "adminHiddenTags" not in html
    assert "hidden_tags" not in html

    client.post("/auth/login", data={"identifier": "bossp_author", "password": "pw"})
    html = client.get(f"/card/{cid}").get_data(as_text=True)
    assert "adminHiddenTags" not in html
    assert "hidden_tags" not in html
    client.get("/auth/logout")

    client.post("/auth/login", data={"identifier": "bossp", "password": "pw"})
    html = client.get(f"/card/{cid}").get_data(as_text=True)
    assert "adminHiddenTags" in html
    assert "hidden_tags" in html


def test_non_admin_cannot_use_shared_endpoint(app, client):
    with app.app_context():
        _admin, author, c = _admin_and_card("bossn", "ZZNOAUTH")
        cid = c.id
    client.post("/auth/login", data={"identifier": "bossn_author", "password": "pw"})
    r = client.post(f"/admin/cards/{cid}/hidden-tags", data={"hidden_tags": [REDUCE_BOOST]})
    assert r.status_code in (302, 403, 404)
    with app.app_context():
        assert db.session.get(Card, cid).hidden_tags == []
