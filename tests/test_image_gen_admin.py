"""生图模型管理：同名多配置（活动免费版）、编辑、模型级 API 通道。

覆盖：
- 同一调用名可添加多条（如活动免费版），不再被唯一约束拦截；
- 新增/编辑支持模型级 api_base_url / api_key，编辑可清除回退全局；
- effective_credentials 的「模型优先、全局兜底」解析；
- 模型与全局均未配置 API 时 /image-gen/generate 直接拒绝且不创建任务。
"""

import pytest
from app import create_app, db
from app.config import Config
from app.models import GenerationModel, GenerationTask
from app.models.user import User
from app.services.image_gen_service import effective_credentials
from app.services.site_service import get_site_config
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


def _make_admin():
    u = User(
        username="admin_img", nickname="管", email="admin_img@example.com",
        role="super_admin",
    )
    u.set_password("pass123")
    db.session.add(u)
    db.session.commit()
    return u


def _login_admin(client):
    client.post("/auth/login", data={"identifier": "admin_img", "password": "pass123"})


def _add_model(client, **overrides):
    data = {
        "name": "gpt-image-1",
        "display_name": "恭喜",
        "points_per_image": "5",
        "enabled": "1",
    }
    data.update(overrides)
    return client.post("/admin/image-models", data=data)


def test_add_duplicate_call_name_allowed(app, client):
    """同一调用名可添加多条（活动免费版），提示「已存在」的拦截已移除。"""
    with app.app_context():
        _make_admin()
    _login_admin(client)

    assert _add_model(client, display_name="恭喜", points_per_image="5").status_code == 302
    # 同名第二条：活动免费版（0 积分）
    assert _add_model(client, display_name="恭喜（活动免费）", points_per_image="0").status_code == 302

    with app.app_context():
        rows = (
            GenerationModel.query.filter_by(name="gpt-image-1")
            .order_by(GenerationModel.id)
            .all()
        )
        assert len(rows) == 2
        assert [r.points_per_image for r in rows] == [5, 0]
        assert [r.display_name for r in rows] == ["恭喜", "恭喜（活动免费）"]

    page = client.get("/admin/image-models")
    assert page.status_code == 200
    assert b"gpt-image-1" in page.data
    assert "恭喜（活动免费）".encode() in page.data


def test_add_model_with_own_api_channel(app, client):
    """新增时可直接填写模型级 API，独立于全局通道。"""
    with app.app_context():
        _make_admin()
    _login_admin(client)

    assert (
        _add_model(
            client,
            name="grok-image-1",
            display_name="Grok 生图",
            points_per_image="3",
            api_base_url="https://api.x.ai/v1",
            api_key="sk-own-xyz",
        ).status_code
        == 302
    )

    with app.app_context():
        m = GenerationModel.query.filter_by(name="grok-image-1").one()
        assert m.api_base_url == "https://api.x.ai/v1"
        assert m.api_key == "sk-own-xyz"


def test_edit_model_fields_and_api_override(app, client):
    with app.app_context():
        _make_admin()
        m = GenerationModel(
            name="gpt-image-1", display_name="恭喜", points_per_image=5, enabled=True
        )
        db.session.add(m)
        db.session.commit()
        mid = m.id
    _login_admin(client)

    # 编辑基础字段 + 设置模型级 API（密钥回显占位，不在页面中出现明文）
    r = client.post(
        f"/admin/image-models/{mid}/edit",
        data={
            "name": "gpt-image-1",
            "display_name": "恭喜 v2",
            "points_per_image": "8",
            "enabled": "1",
            "api_base_url": "https://other.example.com/v1",
            "api_key": "sk-test-123",
        },
    )
    assert r.status_code == 302
    with app.app_context():
        m = db.session.get(GenerationModel, mid)
        assert m.display_name == "恭喜 v2"
        assert m.points_per_image == 8
        assert m.api_base_url == "https://other.example.com/v1"
        assert m.api_key == "sk-test-123"

    page = client.get(f"/admin/image-models/{mid}/edit")
    assert page.status_code == 200
    assert b"sk-test-123" not in page.data  # 密钥不回显

    # 清除自定义 API：置空回退全局
    r = client.post(
        f"/admin/image-models/{mid}/edit",
        data={
            "name": "gpt-image-1",
            "display_name": "恭喜 v3",
            "points_per_image": "2",
            "enabled": "0",
            "clear_api_base_url": "1",
            "clear_api_key": "1",
        },
    )
    assert r.status_code == 302
    with app.app_context():
        m = db.session.get(GenerationModel, mid)
        assert m.display_name == "恭喜 v3"
        assert m.points_per_image == 2
        assert m.enabled is False
        assert m.api_base_url is None
        assert m.api_key is None


def test_non_admin_cannot_manage_models(app, client):
    with app.app_context():
        u = User(username="plain_u", nickname="普", email="plain_u@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()
    client.post("/auth/login", data={"identifier": "plain_u", "password": "pass123"})
    assert client.get("/admin/image-models").status_code == 403
    assert _add_model(client).status_code == 403


def test_effective_credentials_model_first_global_fallback(app):
    with app.app_context():
        cfg = get_site_config()
        cfg.image_base_url = "https://global.example.com/v1"
        cfg.image_api_key = "sk-global"
        db.session.commit()

        # 模型未配置 -> 回退全局
        m1 = GenerationModel(name="a", display_name="A", api_base_url=None, api_key="")
        assert effective_credentials(m1, cfg) == ("https://global.example.com/v1", "sk-global")

        # 模型配置了独立通道 -> 模型优先
        m2 = GenerationModel(
            name="b",
            display_name="B",
            api_base_url="https://other.example.com/v1",
            api_key="sk-own",
        )
        assert effective_credentials(m2, cfg) == ("https://other.example.com/v1", "sk-own")

        # 模型只覆盖地址、密钥缺失 -> 地址用模型的，密钥回退全局
        m3 = GenerationModel(name="c", display_name="C", api_base_url="https://x/v1", api_key=None)
        assert effective_credentials(m3, cfg) == ("https://x/v1", "sk-global")

        # 全局清空且模型未配置 -> 空
        cfg.image_base_url = None
        cfg.image_api_key = None
        db.session.commit()
        assert effective_credentials(m1, cfg) == ("", "")


def test_generate_rejects_when_no_api_configured(app, client):
    """模型与全局均未配置 API 时，/image-gen/generate 直接 400 拒绝且不创建任务。"""
    with app.app_context():
        _make_admin()
        u = User(username="gen_u", nickname="用", email="gen_u@example.com")
        u.set_password("pass123")
        db.session.add(u)
        m = GenerationModel(
            name="gpt-image-1", display_name="恭喜", points_per_image=5, enabled=True
        )
        db.session.add(m)
        db.session.commit()
        mid = m.id
        assert GenerationTask.query.count() == 0

    client.post("/auth/login", data={"identifier": "gen_u", "password": "pass123"})
    r = client.post(
        "/image-gen/generate",
        data={"model": str(mid), "prompt": "测试提示词", "count": "1"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 400
    data = r.get_json()
    assert data["ok"] is False
    with app.app_context():
        assert GenerationTask.query.count() == 0  # 未创建异步任务
