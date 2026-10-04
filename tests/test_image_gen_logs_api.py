"""App 生图历史接口 `/api/v1/image-gen/logs`：新增 `error` 字段 + 可选返回失败记录。

背景：App 的「生图详情」原本只是一个图片查看器，而且没有产图的记录（失败）连点都点不开、
错误原因根本看不到。补齐详情页需要服务端两样支持：

* `error` 字段（失败原因）；
* 失败记录本身 —— 但**默认绝不能返回**：老版本 App 的历史瀑布流只认图片，多出无图条目
  会渲染成空白卡片。所以做成可选参数 `?include_failed=1`，新 App 主动要。

本文件同时守住"默认行为不变"（老客户端零影响）。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models import GenerationLog, GenerationModel
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
    assert app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite")
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


def _user(name="ig_logs_user"):
    u = User(username=name, nickname="生图用户", email=f"{name}@example.com")
    u.set_password("pass123")
    db.session.add(u)
    db.session.commit()
    return u


def _model():
    m = GenerationModel(
        name="gpt-image-1",
        display_name="测试模型",
        points_per_image=1,
        enabled=True,
    )
    db.session.add(m)
    db.session.commit()
    return m


def _log(user, model, *, status, error=None, prompt="一只猫", count=1):
    images = json.dumps(
        ["data:image/webp;base64,UklGRiIAAABXRUJQVlA4IBYAAAAwAQCdASoBAAEAAUAmJaQAA3AA/vuUAAA="]
        * (count if status != "failed" else 0)
    )
    row = GenerationLog(
        user_id=user.id,
        model_id=model.id,
        model_name=model.display_name,
        prompt=prompt,
        size="1024x1024",
        count=count,
        references_count=0,
        status=status,
        images=images,
        points_spent=count,
        error=error,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _token(client, identifier="ig_logs_user"):
    r = client.post("/api/v1/auth/token", json={"identifier": identifier, "password": "pass123"})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["data"]["token"]


def _items(client, token, query=""):
    r = client.get(f"/api/v1/image-gen/logs{query}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]["items"]


def test_default_response_hides_failed_logs_and_keeps_old_fields(app, client):
    """默认行为与以前一致：失败记录不返回；原有字段一个不少，只多了 error。"""
    with app.app_context():
        u = _user()
        m = _model()
        _log(u, m, status="success", prompt="成功的那张")
        _log(u, m, status="failed", error="生图接口错误 (400): 测试失败原因")
        token = None
    token = _token(client)

    items = _items(client, token)
    assert len(items) == 1, "默认不应返回失败记录（老客户端的历史瀑布流只认图片）"
    assert items[0]["prompt"] == "成功的那张"
    for key in (
        "id",
        "first_image",
        "images",
        "references",
        "prompt",
        "model_name",
        "size",
        "count",
        "points_spent",
        "status",
        "created_at",
        "error",
    ):
        assert key in items[0], f"响应缺少字段 {key}"
    assert items[0]["error"] == "", "成功记录的 error 应为空串"


def test_include_failed_returns_failed_logs_with_error(app, client):
    """显式带 include_failed=1：失败记录也回来，并带上失败原因（详情页要显示）。"""
    with app.app_context():
        u = _user()
        m = _model()
        ok = _log(u, m, status="success", prompt="成功的那张")
        bad = _log(u, m, status="failed", error="生图接口错误 (400): 测试失败原因")
        ok_id, bad_id = ok.id, bad.id
    token = _token(client)

    items = _items(client, token, "?include_failed=1")
    by_id = {it["id"]: it for it in items}
    assert set(by_id) == {ok_id, bad_id}

    failed = by_id[bad_id]
    assert failed["status"] == "failed"
    assert failed["error"] == "生图接口错误 (400): 测试失败原因"
    assert failed["images"] == [] and failed["first_image"] == "", "失败记录没有产出图"
    assert failed["prompt"], "失败记录同样要带提示词（详情页要显示）"


def test_partial_logs_are_returned_by_default(app, client):
    """partial（部分完成）不是失败：默认就要返回，不能被新逻辑误伤。"""
    with app.app_context():
        u = _user()
        m = _model()
        _log(u, m, status="partial", prompt="部分完成")
    token = _token(client)
    items = _items(client, token)
    assert [it["prompt"] for it in items] == ["部分完成"]


def test_other_users_failed_logs_are_never_exposed(app, client):
    """带 include_failed 也只返回自己的记录。"""
    with app.app_context():
        me = _user("ig_me")
        other = _user("ig_other")
        m = _model()
        _log(me, m, status="success", prompt="我的成功记录")
        _log(other, m, status="failed", error="别人的失败原因")
    token = _token(client, "ig_me")

    items = _items(client, token, "?include_failed=1")
    assert [it["prompt"] for it in items] == ["我的成功记录"]
    assert all(it["error"] != "别人的失败原因" for it in items)
