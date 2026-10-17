"""网页版发布入口（/worlds/publish）。

与 App 的 POST /api/v1/worlds 共用 publish_world 一个函数，所以这里主要守
「入口存在 + 两端口径一致」：需要登录、粘 JSON 能发、传文件也能发、
坏 JSON 给可读错误、重复提交幂等。
"""

import json
import re

import pytest
from app import create_app, db
from app.config import Config
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
        {"name": name, "summary": "s", "entries": [{"id": "e1", "name": "王都"}]},
        ensure_ascii=False,
    )


def _login(app, client):
    with app.app_context():
        u = User(username="wp", nickname="作者", email="wp@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()
    client.post("/auth/login", data={"identifier": "wp", "password": "pass123"})


def test_requires_login(app, client):
    resp = client.get("/worlds/publish")
    assert resp.status_code in (302, 401)


def test_form_renders(app, client):
    _login(app, client)
    html = client.get("/worlds/publish").get_data(as_text=True)
    assert "发布世界卡" in html
    assert 'name="payload"' in html


def test_publish_from_textarea(app, client):
    _login(app, client)
    resp = client.post("/worlds/publish", data={"payload": _payload()})
    assert resp.status_code in (200, 302)
    with app.app_context():
        world = WorldCard.query.filter_by(name="幻想大陆").first()
        assert world is not None
        assert world.status == "pending"


def test_publish_from_file(app, client):
    _login(app, client)
    resp = client.post(
        "/worlds/publish",
        data={"file": (io_bytes(_payload("文件世界")), "x.dnaworld")},
        content_type="multipart/form-data",
    )
    assert resp.status_code in (200, 302)
    with app.app_context():
        assert WorldCard.query.filter_by(name="文件世界").first() is not None


def io_bytes(text):
    import io

    return io.BytesIO(text.encode("utf-8"))


def _flashes(html):
    """从页面里取出 flash 消息。

    注意：base.html 把 flash 渲染成 var msgs = {{ messages | tojson }} 注入脚本
    （走右上角 toast），所以消息**不是可见文本**，中文还被 tojson 转成了 unicode 转义。
    只能把那段 JSON 解析回来断言。
    """
    m = re.search(r"var msgs = (\[.*?\]);", html, re.S)
    if not m:
        return []
    return [item[1] for item in json.loads(m.group(1))]


def test_bad_json_shows_error(app, client):
    _login(app, client)
    html = client.post("/worlds/publish", data={"payload": "not json"}).get_data(
        as_text=True
    )
    assert any("合法的 JSON" in (msg or "") for msg in _flashes(html))
    with app.app_context():
        assert WorldCard.query.count() == 0


def test_duplicate_is_idempotent(app, client):
    _login(app, client)
    client.post("/worlds/publish", data={"payload": _payload()})
    client.post("/worlds/publish", data={"payload": _payload()})
    with app.app_context():
        assert WorldCard.query.filter_by(name="幻想大陆").count() == 1
