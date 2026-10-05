"""用户端的「生成失败记录」页 + 日志接口的 `status` 过滤。

用户反馈：web / App 都没有地方看自己**所有失败的生成**及其信息。web 的列表接口还会把
失败记录整条过滤掉（`img_count > 0`），所以连"翻历史找失败"都做不到。

这里守住两件事：
* 新页面 `/image-gen/failed` 只列自己的失败记录，并带上提示词/参数/参考图/原因；
* 两个日志接口新增 `?status=failed`（可选参数）—— **默认行为一个字都不能变**，
  否则老版本 App 的历史瀑布流会收到没有产出图的条目。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models import GenerationLog, GenerationModel
from app.models.user import User
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles

REF_DATA_URL = "data:image/webp;base64,UklGRjoAAABXRUJQVlA4IC4AAAAQAgCdASoIAAgAAUAmJaACdLoB+AH4AAPIAP7tTd/9QWCsy/ew/+jBcBwPeEAA"


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


def _seed(username="ig_fail_user"):
    """一条成功 + 一条失败（失败那条带 2 张参考图）。返回 (ok_id, bad_id)。"""
    u = User(username=username, nickname="失败用户", email=f"{username}@example.com")
    u.set_password("pass123")
    db.session.add(u)
    m = GenerationModel(
        name="gpt-image-1", display_name="G Imagine", points_per_image=1, enabled=True
    )
    db.session.add(m)
    db.session.commit()

    ok = GenerationLog(
        user_id=u.id,
        model_id=m.id,
        model_name=m.display_name,
        prompt="成功的那张",
        size="1024x1024",
        count=1,
        references_count=0,
        status="success",
        images=json.dumps([REF_DATA_URL]),
        points_spent=1,
    )
    bad = GenerationLog(
        user_id=u.id,
        model_id=m.id,
        model_name=m.display_name,
        prompt="一只戴帽子的猫，赛博朋克风格",
        size="768x1024",
        count=2,
        references_count=2,
        status="failed",
        images="[]",
        reference_images=json.dumps([REF_DATA_URL, REF_DATA_URL]),
        points_spent=0,
        error="生图接口错误 (400): Invalid request: multipart: NextPart: EOF",
    )
    db.session.add_all([ok, bad])
    db.session.commit()
    return ok.id, bad.id


def _login(client, identifier="ig_fail_user", password="pass123"):
    r = client.post("/auth/login", data={"identifier": identifier, "password": password})
    assert r.status_code in (200, 302), r.status_code


def test_failed_page_lists_all_failures_with_info(app, client):
    """「生成失败记录」页：列出自己的失败记录，并带上提示词/参数/参考图/原因。"""
    with app.app_context():
        _ok_id, bad_id = _seed()
    _login(client)

    html = client.get("/image-gen/failed").get_data(as_text=True)
    assert "一只戴帽子的猫，赛博朋克风格" in html
    assert "multipart: NextPart: EOF" in html
    assert "G Imagine" in html
    assert "768x1024" in html
    assert f"/image-gen/reference/{bad_id}/0" in html
    assert "成功的那张" not in html, "成功记录不该出现在失败记录页"
    assert f"/image-gen/logs/{bad_id}" in html, "要能从列表点进详情"


def test_failed_page_requires_login(client):
    r = client.get("/image-gen/failed")
    assert r.status_code == 302, "未登录应跳登录页"


def test_failed_page_does_not_leak_other_users(app, client):
    """只能看到自己的失败记录。"""
    with app.app_context():
        _seed(username="ig_fail_me")
        _seed(username="ig_fail_other")
        other = User.query.filter_by(username="ig_fail_other").one()
        db.session.add(
            GenerationLog(
                user_id=other.id,
                model_name="G Imagine",
                prompt="别人的失败提示词",
                size="1024x1024",
                count=1,
                references_count=0,
                status="failed",
                images="[]",
                points_spent=0,
                error="别人的失败原因",
            )
        )
        db.session.commit()
    _login(client, "ig_fail_me")

    html = client.get("/image-gen/failed").get_data(as_text=True)
    assert "别人的失败提示词" not in html
    assert "别人的失败原因" not in html


def test_web_api_status_filter_includes_failed(app, client):
    """web 列表接口：默认仍不返回失败记录（不破坏既有行为），带 status=failed 才返回。"""
    with app.app_context():
        _ok_id, bad_id = _seed()
    _login(client)

    default_items = client.get("/image-gen/api/logs").get_json()["items"]
    assert [it["prompt"] for it in default_items] == ["成功的那张"], "默认行为不能变"

    failed_items = client.get("/image-gen/api/logs?status=failed").get_json()["items"]
    assert [it["id"] for it in failed_items] == [bad_id]
    assert failed_items[0]["error"].startswith("生图接口错误")
    assert failed_items[0]["images"] == [] and failed_items[0]["first_image"] == ""


def test_app_api_status_filter_includes_failed(app, client):
    """App 接口同样支持 ?status=failed（App 的失败记录页靠它翻页）。"""
    with app.app_context():
        _ok_id, bad_id = _seed()
    r = client.post(
        "/api/v1/auth/token", json={"identifier": "ig_fail_user", "password": "pass123"}
    )
    token = r.get_json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}

    default_items = client.get("/api/v1/image-gen/logs", headers=headers).get_json()["data"][
        "items"
    ]
    assert [it["prompt"] for it in default_items] == ["成功的那张"], "默认行为不能变"

    failed_items = client.get("/api/v1/image-gen/logs?status=failed", headers=headers).get_json()[
        "data"
    ]["items"]
    assert [it["id"] for it in failed_items] == [bad_id]
    assert failed_items[0]["error"].startswith("生图接口错误")
    assert failed_items[0]["references"], "失败记录也要带上参考图 URL（详情页要显示）"
