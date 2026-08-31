import pytest
from app import create_app, db
from app.config import Config
from app.models import TeaPost, User
from app.routes.api import _make_token
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles


@compiles(LONGTEXT, "sqlite")
def compile_longtext_sqlite(type_, compiler, **kw):
    return "TEXT"


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "test-secret-key"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    app = create_app(TestConfig)
    assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"), \
        f"🧨 测试连到了非 SQLite 数据库！{app.config['SQLALCHEMY_DATABASE_URI']}"
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


def _headers(app, user_id):
    with app.app_context():
        return {"Authorization": f"Bearer {_make_token(user_id)}"}


def test_profile_teahouse_returns_top_level_posts(app, client):
    """GET /api/v1/users/<username>/teahouse 返回该用户发布的顶级帖子。"""
    with app.app_context():
        u = User(username="tea_author", nickname="茶馆作者", email="tea@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()

        top = TeaPost(user_id=u.id, content="我的茶馆帖子")
        db.session.add(top)
        db.session.commit()
        # 先生成顶级帖 id，再创建以其为父帖的回复，确保 parent_id 生效。
        reply = TeaPost(user_id=u.id, content="这是一条回复", parent_id=top.id)
        db.session.add(reply)
        db.session.commit()
        top_id, reply_id = top.id, reply.id

    # 匿名访问也应返回帖子（公开只读）。
    r = client.get("/api/v1/users/tea_author/teahouse")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["ok"] is True
    items = body["data"]["items"]
    ids = [i["id"] for i in items]
    # 顶级帖子应返回，回复（parent_id 非空）不应出现在该列表。
    assert top_id in ids
    assert reply_id not in ids
    assert body["data"]["has_next"] is False


def test_profile_teahouse_hides_hidden_posts_for_others(app, client):
    """他人访问时隐藏被管理员隐藏的帖子。"""
    with app.app_context():
        u = User(username="tea_hidden", nickname="隐藏作者", email="tea_h@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()
        visible = TeaPost(user_id=u.id, content="可见帖", is_hidden=False)
        hidden = TeaPost(user_id=u.id, content="被隐藏帖", is_hidden=True)
        db.session.add_all([visible, hidden])
        db.session.commit()
        visible_id, hidden_id = visible.id, hidden.id
        author_id = u.id

    # 匿名/他人访问：隐藏帖不出现在列表。
    r = client.get("/api/v1/users/tea_hidden/teahouse")
    ids = [i["id"] for i in r.get_json()["data"]["items"]]
    assert visible_id in ids
    assert hidden_id not in ids

    # 作者本人访问：能看到自己的隐藏帖。
    r = client.get(
        "/api/v1/users/tea_hidden/teahouse", headers=_headers(app, author_id)
    )
    ids = [i["id"] for i in r.get_json()["data"]["items"]]
    assert visible_id in ids
    assert hidden_id in ids


def test_profile_teahouse_unknown_user_404(app, client):
    """不存在的用户返回 404。"""
    r = client.get("/api/v1/users/no_such_user/teahouse")
    assert r.status_code == 404
