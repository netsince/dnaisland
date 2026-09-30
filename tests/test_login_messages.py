"""登录错误区分 + 失败限流的契约测试。

需求：登录时分别提示「找不到该用户名/邮箱」与「密码错误」（原为通用提示）。
安全缓解：同一客户端 IP 每 60 秒最多 5 次失败登录（成功不计入）。
"""

import json
import re

import pytest
from app import create_app, db
from app.config import Config
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


@pytest.fixture
def user(app):
    with app.app_context():
        u = User(username="alice", nickname="Alice", email="alice@x.com")
        u.set_password("correct-horse")
        db.session.add(u)
        db.session.commit()
        return u.id


def _web_login(client, identifier, password, ip=None):
    kwargs = {"environ_overrides": {"REMOTE_ADDR": ip}} if ip else {}
    return client.post(
        "/auth/login", data={"identifier": identifier, "password": password}, **kwargs
    )


def _api_login(client, identifier, password, ip=None):
    kwargs = {"environ_overrides": {"REMOTE_ADDR": ip}} if ip else {}
    return client.post(
        "/api/v1/auth/token",
        json={"identifier": identifier, "password": password},
        **kwargs,
    )


_FLASH_RE = re.compile(r"var msgs = (\[.*?\]);", re.S)


def _flashed_messages(html: str) -> list:
    """从渲染结果里取出 flash 消息文本。

    base.html 把 flash 以 `var msgs = {{ messages | tojson }}` 的形式交给 toast 脚本，
    中文会被 tojson 转义成 Unicode 转义序列，因此必须解析 JSON 而不能直接字符串包含判断。
    """
    m = _FLASH_RE.search(html)
    if not m:
        return []
    return [item[1] for item in json.loads(m.group(1)) if item and len(item) > 1]


# ---------------------------------------------------------------------------
# 文案区分
# ---------------------------------------------------------------------------

def test_web_unknown_identifier_message(app, client, user):
    r = _web_login(client, "nobody", "whatever")
    assert r.status_code == 200
    assert "找不到该用户名/邮箱" in _flashed_messages(r.get_data(as_text=True))


def test_web_wrong_password_message(app, client, user):
    r = _web_login(client, "alice", "wrong")
    assert r.status_code == 200
    msgs = _flashed_messages(r.get_data(as_text=True))
    assert "密码错误" in msgs
    assert "找不到该用户名/邮箱" not in msgs


def test_web_login_by_email_reports_wrong_password(app, client, user):
    r = _web_login(client, "alice@x.com", "wrong")
    assert "密码错误" in _flashed_messages(r.get_data(as_text=True))


def test_web_success_redirects(app, client, user):
    r = _web_login(client, "alice", "correct-horse")
    assert r.status_code == 302


def test_api_unknown_identifier_message(app, client, user):
    r = _api_login(client, "nobody", "whatever")
    assert r.status_code == 401
    assert r.get_json()["error"] == "找不到该用户名/邮箱"


def test_api_wrong_password_message(app, client, user):
    r = _api_login(client, "alice", "wrong")
    assert r.status_code == 401
    assert r.get_json()["error"] == "密码错误"


def test_api_success_returns_token(app, client, user):
    r = _api_login(client, "alice", "correct-horse")
    assert r.status_code == 200
    assert r.get_json()["data"]["token"]


# ---------------------------------------------------------------------------
# 失败限流
# ---------------------------------------------------------------------------

def test_api_throttled_after_five_failures(app, client, user):
    for _ in range(5):
        assert _api_login(client, "nobody", "x").status_code == 401
    r = _api_login(client, "nobody", "x")
    assert r.status_code == 429
    assert "过于频繁" in r.get_json()["error"]


def test_web_throttled_after_five_failures(app, client, user):
    for _ in range(5):
        _web_login(client, "nobody", "x")
    r = _web_login(client, "nobody", "x")
    assert any("过于频繁" in m for m in _flashed_messages(r.get_data(as_text=True)))


def test_throttle_applies_before_credential_check(app, client, user):
    """第 6 次即使密码正确也应被限流（限流先于鉴权生效）。"""
    for _ in range(5):
        _api_login(client, "alice", "wrong")
    r = _api_login(client, "alice", "correct-horse")
    assert r.status_code == 429


def test_successful_logins_do_not_count_toward_throttle(app, client, user):
    for _ in range(8):
        assert _api_login(client, "alice", "correct-horse").status_code == 200


def test_throttle_is_per_ip(app, client, user):
    for _ in range(5):
        _api_login(client, "nobody", "x", ip="10.0.0.1")
    assert _api_login(client, "nobody", "x", ip="10.0.0.1").status_code == 429
    # 另一个 IP 不受影响
    assert _api_login(client, "nobody", "x", ip="10.0.0.2").status_code == 401


def test_web_and_api_share_throttle_bucket(app, client, user):
    """网页与 App 走同一套限流口径，混合尝试合并计数。"""
    for _ in range(3):
        _web_login(client, "nobody", "x")
    for _ in range(2):
        _api_login(client, "nobody", "x")
    assert _api_login(client, "nobody", "x").status_code == 429
