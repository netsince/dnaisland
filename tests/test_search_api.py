"""搜索接口回归测试：角色卡 / 用户 / 茶馆帖 / 联想建议。

覆盖核心场景：正常搜索返回结果，以及生产 MySQL 缺全文索引时
（FULLTEXT MATCH...AGAINST 抛错）自动回退 LIKE，搜索不空结果 / 不 500。
"""
import pytest
from app import create_app, db
from app.config import Config
from app.models.card import Card
from app.models.user import User
from app.models.teahouse import TeaPost
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
    with app.app_context():
        author = User(username="s_author", nickname="作者甲乙丙", email="s_a@example.com")
        author.set_password("pass123")
        db.session.add(author)
        db.session.commit()
        card = Card(id="card-s-1", author_id=author.id, name="测试角色卡",
                    persona="某个角色设定", intro="简介内容", status="approved")
        db.session.add(card)
        db.session.commit()
        post = TeaPost(user_id=author.id, content="这是一条茶馆帖内容用于搜索", parent_id=None)
        db.session.add(post)
        db.session.commit()


def test_search_endpoints(app, client):
    """四个搜索端点正常返回结果。"""
    _seed(app)

    r = client.get("/api/v1/cards/search?q=角色")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert len(r.get_json()["data"]["items"]) == 1

    r = client.get("/api/v1/users/search?q=甲乙")
    assert r.status_code == 200
    assert len(r.get_json()["data"]["items"]) == 1

    r = client.get("/api/v1/teahouse/search?q=茶馆帖")
    assert r.status_code == 200
    assert len(r.get_json()["data"]["items"]) == 1

    r = client.get("/api/v1/search/suggest?q=角色")
    assert r.status_code == 200
    assert len(r.get_json()["data"]["cards"]) == 1


def test_search_empty_query_returns_error(app, client):
    """缺少 q 时按契约返回 400 错误（非空结果）。"""
    _seed(app)
    for path in ("/api/v1/cards/search", "/api/v1/users/search", "/api/v1/teahouse/search"):
        r = client.get(path)
        assert r.status_code == 400
        assert r.get_json()["ok"] is False


def test_fulltext_failure_falls_back_to_like(app, client, monkeypatch):
    """生产 MySQL 启用 FULLTEXT 但索引缺失（MATCH...AGAINST 抛错）时，
    自动回退 LIKE，搜索仍返回正确结果（避免 500 → 客户端空结果）。"""
    _seed(app)

    from app.routes import main as main_mod

    # 强制走 FULLTEXT 分支（SQLite 上 MATCH...AGAINST 必然抛错，等价于 MySQL 缺索引）。
    monkeypatch.setattr(main_mod, "_fulltext_enabled", lambda: True)

    r = client.get("/api/v1/cards/search?q=角色")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert len(body["data"]["items"]) == 1

    r = client.get("/api/v1/teahouse/search?q=茶馆帖")
    assert r.status_code == 200
    assert len(r.get_json()["data"]["items"]) == 1

    r = client.get("/api/v1/search/suggest?q=角色")
    assert r.status_code == 200
    assert len(r.get_json()["data"]["cards"]) == 1
