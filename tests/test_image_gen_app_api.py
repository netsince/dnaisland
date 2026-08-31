"""App 端生图接口 /api/v1/image-gen/generate 冒烟测试。

回归覆盖：App 的 generateImage 发送 JSON 载荷（model_id 为 int）。
历史 bug：路由里 `data.get("model_id", type=int)` 用普通 dict 调用，
dict.get 不接受 `type=` 关键字，导致 TypeError -> HTTP 500。
"""

import pytest
from app import create_app, db
from app.config import Config
from app.models import GenerationModel, GenerationTask
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


def _auth_token(client):
    """通过 App 登录接口取 JWT（与 App 端一致）。"""
    r = client.post("/api/v1/auth/token", json={
        "identifier": "gen_api_user",
        "password": "pass123",
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    return data["data"]["token"]


def test_app_generate_returns_ok_not_500(app, client):
    """带合法 model_id 的 JSON 载荷不应再触发 TypeError -> 500。"""
    with app.app_context():
        u = User(username="gen_api_user", nickname="生", email="gen@example.com")
        u.set_password("pass123")
        u.points = 100
        db.session.add(u)
        m = GenerationModel(
            name="gpt-image-1", display_name="恭喜", points_per_image=5, enabled=True,
            api_base_url="https://mock.example.com/v1", api_key="sk-mock",
        )
        db.session.add(m)
        db.session.commit()
        mid = m.id

    token = _auth_token(client)
    r = client.post(
        "/api/v1/image-gen/generate",
        json={
            "prompt": "一只猫",
            "model_id": mid,  # int，App 端实际发送
            "size": "1:1",
            "count": 1,
            "references": [],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    # 不应再是 500；该场景凭证已配置，应返回 ok + task_id。
    assert r.status_code == 200, (r.status_code, r.get_json())
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["task_id"] > 0
    with app.app_context():
        assert GenerationTask.query.count() == 1


def test_app_generate_missing_model_id_returns_400(app, client):
    """缺 model_id 时不应崩溃，应优雅返回 400。"""
    with app.app_context():
        u = User(username="gen_api_user", nickname="生", email="gen@example.com")
        u.set_password("pass123")
        db.session.add(u)
        db.session.commit()

    token = _auth_token(client)
    r = client.post(
        "/api/v1/image-gen/generate",
        json={"prompt": "一只猫", "count": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 400, (r.status_code, r.get_json())
    assert r.get_json()["ok"] is False
