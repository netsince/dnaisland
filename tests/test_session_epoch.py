"""会话代数（users.session_epoch）：改密码 = 踢掉所有旧设备。

背景：网页用 Flask-Login 的**签名 Cookie**、App 用**无状态 JWT**，服务端不存会话表，
所以光改密码不会让已经发出去的凭证失效（账号被盗后对方那台仍在线）。加一列
`session_epoch`：每改一次密码 +1，网页会话与 JWT 都带上它，请求时比对，不一致即失效。

**关键的安全底线（部署不踢人）**：缺失的 epoch 一律当 0。加这一列之前发出的 Cookie 与
token 里没有这个字段，若按"必须相等"硬判，部署瞬间会让所有网页用户与 App 用户掉线。
本文件专门守住这条：老 Cookie / 老 token 在 epoch 仍为 0 时照常可用，只有真的改过密码
（epoch ≥ 1）才失效。
"""

import time

import jwt
import pytest
from app import create_app, db
from app.config import Config
from app.models import User
from app.routes.api import _soft_auth
from app.services.verification_code_service import create_code
from flask import g
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
    # 注意：这里**刻意不长期持有 app context**。
    # 若 fixture 一直 `with app.app_context()`，Flask 测试客户端会复用它（Flask 只在
    # 没有同 app 的上下文时才新建），于是请求与测试共用同一个 SQLAlchemy session ——
    # session 的 identity map 里会留着"改密码之前"的 User 实例，钩子读到的 epoch 永远是旧值，
    # 用例会假失败。生产里每个请求各有独立 session，所以这里也照那个样子来。
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


def _user(email="u@x.com", password="pass123456"):
    u = User(username=email.split("@")[0], nickname="用户", email=email, password_hash="x")
    db.session.add(u)
    db.session.commit()
    u.set_password(password)  # 会 +1（新用户从 0 → 1）
    db.session.commit()
    return u


def _web_login(client, email="u@x.com", password="pass123456", remember=True):
    return client.post(
        "/auth/login",
        data={"identifier": email, "password": password, "remember": "y" if remember else ""},
    )


def _api_token(client, email="u@x.com", password="pass123456"):
    r = client.post("/api/v1/auth/token", json={"identifier": email, "password": password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["data"]["token"]


def _me(client, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get("/api/v1/auth/me", headers=headers)


# ---------------------------------------------------------------------------
# 模型层
# ---------------------------------------------------------------------------


def test_set_password_bumps_epoch(app):
    with app.app_context():
        u = _user()
        before = u.session_epoch
        u.set_password("another-pass")
        db.session.commit()
        assert u.session_epoch == before + 1, "改密码必须推进会话代数"
        assert u.check_password("another-pass")


# ---------------------------------------------------------------------------
# 网页会话
# ---------------------------------------------------------------------------


def test_web_session_survives_normal_requests(app, client):
    """没改密码的人不受影响（对照组）。"""
    with app.app_context():
        _user()
    assert _web_login(client).status_code == 302
    assert client.get("/auth/logout").status_code == 302  # 已登录 → 直接登出
    # 重新登录后再看一次受保护页面
    _web_login(client)
    assert "/auth/login" not in client.get("/publish/").headers.get("Location", "")


def test_web_session_dies_after_password_change(app, client):
    """另一台设备改了密码 → 本设备的 Cookie 立刻失效。"""
    with app.app_context():
        u = _user()
        u_id = u.id
    assert _web_login(client).status_code == 302

    # 模拟"另一处"改了密码（找回密码/后台改密都走 set_password）
    with app.app_context():
        db.session.get(User, u_id).set_password("brand-new-pass")
        db.session.commit()

    r = client.get("/publish/", follow_redirects=False)
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"], (
        "改密码后旧会话必须被登出"
    )


def test_legacy_cookie_without_epoch_is_not_killed(app, client):
    """部署安全底线：老 Cookie（session 里没有 session_epoch）在 epoch=0 时照常可用。"""
    with app.app_context():
        u = _user()
        u.session_epoch = 0  # 模拟"从没改过密码"的存量用户
        db.session.commit()
        u_id = u.id

    # 手工构造一个"老式"会话：只有 flask_login 的键，没有我们的 session_epoch
    with client.session_transaction() as sess:
        sess["_user_id"] = str(u_id)
        sess["_fresh"] = True
        assert "session_epoch" not in sess, "本用例前提：session 里没有 session_epoch"

    r = client.get("/publish/", follow_redirects=False)
    assert r.status_code == 200, "老 Cookie 不该在部署瞬间被踢下线"


def test_legacy_cookie_dies_once_epoch_advances(app, client):
    with app.app_context():
        u = _user()
        u.session_epoch = 0
        db.session.commit()
        u_id = u.id

    with client.session_transaction() as sess:
        sess["_user_id"] = str(u_id)
        sess["_fresh"] = True

    with app.app_context():
        db.session.get(User, u_id).set_password("changed-later")
        db.session.commit()

    r = client.get("/publish/", follow_redirects=False)
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]


def test_remember_me_cookie_cannot_revive_a_dead_session(app, client):
    """「记住我」是长效凭证：改密码后它也不能把人重新登进来。"""
    with app.app_context():
        u = _user()
        u_id = u.id
    assert _web_login(client, remember=True).status_code == 302

    client.delete_cookie("session")  # 只留下 remember_token
    with app.app_context():
        db.session.get(User, u_id).set_password("changed-after-remember")
        db.session.commit()

    r = client.get("/publish/", follow_redirects=False)
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]


def test_password_reset_kicks_other_web_sessions(app, client):
    """端到端：**另一台设备**用找回密码改了密码 → 本设备被登出。

    必须用两个 client：登录状态下访问找回密码会被重定向走（与登录/注册页一致的既有设计），
    真实的"找回密码"场景本来就是"人在另一台设备/浏览器上操作"。
    """
    with app.app_context():
        _user(email="kick@x.com")
        code = create_code("kick@x.com", purpose="reset")

    assert _web_login(client, email="kick@x.com").status_code == 302  # 设备 A 登录着

    other = app.test_client()  # 设备 B
    r = other.post(
        "/auth/reset-password",
        data={
            "email": "kick@x.com",
            "code": code,
            "password": "reset-new-pass",
            "confirm_password": "reset-new-pass",
        },
    )
    assert r.status_code == 302, "设备 B 的重置应当成功"

    r = client.get("/publish/", follow_redirects=False)
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"], (
        "设备 A 的旧会话必须失效"
    )


# ---------------------------------------------------------------------------
# App JWT
# ---------------------------------------------------------------------------


def test_api_token_survives_when_password_unchanged(app, client):
    with app.app_context():
        _user()
    token = _api_token(client)
    assert _me(client, token).status_code == 200


def test_api_token_dies_after_password_change(app, client):
    with app.app_context():
        u = _user()
        u_id = u.id
    token = _api_token(client)

    with app.app_context():
        db.session.get(User, u_id).set_password("another-secret")
        db.session.commit()

    r = _me(client, token)
    assert r.status_code == 401
    assert "登录已过期" in r.get_json()["error"], "文案要能让 App 走它既有的重新登录流程"


def test_fresh_token_works_after_password_change(app, client):
    with app.app_context():
        u = _user()
        u_id = u.id
    with app.app_context():
        db.session.get(User, u_id).set_password("after-change-pass")
        db.session.commit()
    token = _api_token(client, password="after-change-pass")
    assert _me(client, token).status_code == 200


def test_legacy_token_without_epoch_claim_still_works(app, client):
    """部署安全底线：老版本 App 手里没有 epoch 字段的 token，在 epoch=0 时照常可用。"""
    with app.app_context():
        u = _user()
        u.session_epoch = 0
        db.session.commit()
        u_id = u.id

    legacy = jwt.encode(
        {"user_id": u_id, "exp": int(time.time()) + 3600, "iat": int(time.time())},
        app.config["SECRET_KEY"],
        algorithm="HS256",
    )
    assert _me(client, legacy).status_code == 200, "老 token 不该在部署瞬间掉线"


def test_legacy_token_dies_once_epoch_advances(app, client):
    with app.app_context():
        u = _user()
        u.session_epoch = 0
        db.session.commit()
        u_id = u.id

    legacy = jwt.encode(
        {"user_id": u_id, "exp": int(time.time()) + 3600, "iat": int(time.time())},
        app.config["SECRET_KEY"],
        algorithm="HS256",
    )
    with app.app_context():
        db.session.get(User, u_id).set_password("post-deploy-change")
        db.session.commit()
    assert _me(client, legacy).status_code == 401


def test_soft_auth_ignores_stale_token(app, client):
    """可选登录入口也必须校验：否则旧 token 仍能以该用户身份读到私有内容。"""
    with app.app_context():
        u = _user()
        u_id = u.id
        stale = jwt.encode(
            {"user_id": u_id, "epoch": u.session_epoch, "exp": int(time.time()) + 3600},
            app.config["SECRET_KEY"],
            algorithm="HS256",
        )
        db.session.get(User, u_id).set_password("soft-auth-change")
        db.session.commit()

    with app.test_request_context("/api/v1/cards/x", headers={"Authorization": f"Bearer {stale}"}):
        _soft_auth()
        assert getattr(g, "api_user", None) is None, "过期 token 不能以该用户身份继续读数据"


def test_soft_auth_accepts_valid_token(app):
    with app.app_context():
        u = _user()
        good = jwt.encode(
            {"user_id": u.id, "epoch": u.session_epoch, "exp": int(time.time()) + 3600},
            app.config["SECRET_KEY"],
            algorithm="HS256",
        )
    with app.test_request_context("/api/v1/cards/x", headers={"Authorization": f"Bearer {good}"}):
        _soft_auth()
        assert getattr(g, "api_user", None) is not None


def test_refresh_keeps_the_current_epoch(app, client):
    """刷新出来的 token 必须带当前代数（否则刷新会把旧会话"洗白"）。"""
    with app.app_context():
        _user()
    token = _api_token(client)
    r = client.post("/api/v1/auth/refresh", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    fresh = r.get_json()["data"]["token"]
    assert _me(client, fresh).status_code == 200

    with app.app_context():
        u = User.query.filter_by(email="u@x.com").one()
        payload = jwt.decode(fresh, app.config["SECRET_KEY"], algorithms=["HS256"])
        assert payload["epoch"] == u.session_epoch
