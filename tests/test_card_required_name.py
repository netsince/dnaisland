"""发布/编辑角色卡：名称必填。

背景：网页发布表单靠 HTML 的 `required` 拦名称，但服务端 `create_card_from_payload`
没有任何检查；App 端编辑器又是零校验 —— 结果手机端能发出全空的角色卡，
审核队列里堆一堆无标题卡。

现在校验落在服务端的唯一入口上（Web / App / 直连 API 通吃），
App 侧只是加了一道前置提示，规则本身不散落在客户端。
"""

import pytest
from app import create_app, db
from app.config import Config
from app.models.user import User
from app.services.card_edit_service import update_card_from_payload
from app.services.card_publish_service import create_card_from_payload
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


def _author(name):
    u = User(username=name, nickname=name, email=f"{name}@x.com")
    u.set_password("pw")
    db.session.add(u)
    db.session.commit()
    return u


def test_create_rejects_blank_name(app):
    with app.app_context():
        author = _author("n1")
        card, error = create_card_from_payload(author, {"name": "   "})
        assert card is None
        assert error == "请填写角色卡名称"


def test_create_rejects_missing_name(app):
    with app.app_context():
        author = _author("n2")
        card, error = create_card_from_payload(author, {"persona": "只有人设"})
        assert card is None
        assert error == "请填写角色卡名称"


def test_api_publish_returns_400_with_readable_message(app, client):
    with app.app_context():
        _author("n3")
    client.post("/auth/login", data={"identifier": "n3", "password": "pw"})
    r = client.post("/api/v1/cards/publish", json={"name": "  ", "persona": "x"})
    assert r.status_code == 400
    assert "名称" in r.get_json()["error"]


def test_update_keeps_old_name_when_payload_omits_it(app):
    """编辑时留空名字 = 沿用原名，别把「只改图片」这类调用误伤成校验失败。"""
    with app.app_context():
        author = _author("n4")
        card, error = create_card_from_payload(author, {"name": "原名"})
        assert card is not None and error is None
        db.session.commit()

        assert update_card_from_payload(card, {"persona": "只改人设"}) is None
        assert card.name == "原名"


def test_update_rejects_when_card_name_is_empty(app):
    """历史脏数据（名字为空）编辑时必须补名字，别让无标题卡继续流转。"""
    with app.app_context():
        author = _author("n5")
        card, error = create_card_from_payload(author, {"name": "临时名字"})
        assert card is not None and error is None
        card.name = ""
        db.session.commit()

        err = update_card_from_payload(card, {"persona": "x"})
        assert err == "请填写角色卡名称"
