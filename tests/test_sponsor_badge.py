"""赞助者标记（昵称旁红星）回归测试。

网页版通过 Jinja 全局 `is_sponsor` 在昵称旁画红星；JSON API 通过
`_user_public()` 的 `is_sponsor` 字段输出，客户端据此渲染。两边必须同一
口径，且**每请求只查一次**赞助者集合（列表页逐行查库就是 N+1）。

判定逻辑已从 app 工厂的闭包抽到 `app/services/sponsor_service.py`，
这里同时守住"网页版行为不变"。
"""

import pytest
from app import create_app, db
from app.config import Config
from app.models.card import Card
from app.models.sponsor import Sponsor
from app.models.user import User
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
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


def _seed(app):
    """一个赞助者 + 一个普通用户，各带一张已过审角色卡。

    两人昵称都含「大佬」，便于一次搜索命中多个用户来验证批量序列化。
    """
    with app.app_context():
        sponsor = User(username="s_sponsor", nickname="赞助大佬", email="sp@example.com")
        sponsor.set_password("pass123")
        plain = User(username="s_plain", nickname="普通大佬", email="pl@example.com")
        plain.set_password("pass123")
        db.session.add_all([sponsor, plain])
        db.session.commit()

        db.session.add(Sponsor(user_id=sponsor.id, display_name="赞助大佬", amount="¥66.6"))
        db.session.add_all(
            [
                Card(
                    id="card-sp",
                    author_id=sponsor.id,
                    name="赞助者的卡",
                    persona="x",
                    intro="y",
                    status="approved",
                ),
                Card(
                    id="card-pl",
                    author_id=plain.id,
                    name="普通人的卡",
                    persona="x",
                    intro="y",
                    status="approved",
                ),
            ]
        )
        db.session.commit()
        return sponsor.id, plain.id


def test_card_author_payload_carries_is_sponsor(app, client):
    """卡片作者载荷带 is_sponsor：赞助者 true，普通用户 false。"""
    _seed(app)

    r = client.get("/api/v1/cards/card-sp")
    assert r.status_code == 200
    assert r.get_json()["data"]["author"]["is_sponsor"] is True

    r = client.get("/api/v1/cards/card-pl")
    assert r.status_code == 200
    assert r.get_json()["data"]["author"]["is_sponsor"] is False


def test_user_profile_payload_carries_is_sponsor(app, client):
    """用户主页载荷带 is_sponsor（网页个人页与 App 个人页都显示红星）。"""
    _seed(app)

    r = client.get("/api/v1/users/s_sponsor")
    assert r.status_code == 200
    assert r.get_json()["data"]["user"]["is_sponsor"] is True

    r = client.get("/api/v1/users/s_plain")
    assert r.status_code == 200
    assert r.get_json()["data"]["user"]["is_sponsor"] is False


def test_search_suggest_user_hits_carry_is_sponsor(app, client):
    """搜索建议里的用户同样带标记（网页搜索结果也显示红星）。"""
    _seed(app)

    r = client.get("/api/v1/search/suggest?q=大佬")
    assert r.status_code == 200
    users = r.get_json()["data"]["users"]
    assert len(users) == 2
    by_username = {u["username"]: u["is_sponsor"] for u in users}
    assert by_username == {"s_sponsor": True, "s_plain": False}


def test_jinja_global_still_works(app):
    """网页版调用的 Jinja 全局仍可用 —— 判定搬进 service 后行为不变。"""
    sponsor_id, plain_id = _seed(app)

    with app.test_request_context():
        fn = app.jinja_env.globals["is_sponsor"]
        assert fn(sponsor_id) is True
        assert fn(plain_id) is False
        # 空值/0 一律按非赞助者处理（模板里 user 可能为 None）。
        assert fn(None) is False
        assert fn(0) is False


def test_sponsor_set_cached_within_request(app):
    """同一请求内反复判定只查一次库：拿到的是同一个集合对象。"""
    sponsor_id, _ = _seed(app)
    from app.services.sponsor_service import is_sponsor, sponsor_user_ids

    with app.test_request_context():
        first = sponsor_user_ids()
        second = sponsor_user_ids()
        assert first is second, "赞助者集合未按请求缓存（会退化成 N+1）"
        assert sponsor_id in first
        assert is_sponsor(sponsor_id) is True


def test_sponsor_table_queried_only_once_per_list_request(app, client):
    """批量序列化多个用户时，sponsors 表只查一次（防 N+1）。"""
    _seed(app)

    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        if "from sponsors" in statement.lower():
            statements.append(statement)

    event.listen(Engine, "before_cursor_execute", _record)
    try:
        r = client.get("/api/v1/search/suggest?q=大佬")
        assert r.status_code == 200
        assert len(r.get_json()["data"]["users"]) == 2
    finally:
        event.remove(Engine, "before_cursor_execute", _record)

    assert len(statements) == 1, f"sponsors 被查了 {len(statements)} 次：{statements}"


def test_is_sponsor_safe_outside_request_context():
    """完全没有应用上下文时不抛异常，降级为非赞助者（脚本/后台任务会走到）。

    刻意不请求 app fixture —— 就是为了在无上下文的状态下调用。
    """
    from app.services.sponsor_service import is_sponsor_safe

    assert is_sponsor_safe(1) is False
    assert is_sponsor_safe(None) is False
