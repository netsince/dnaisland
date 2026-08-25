"""转发 API（BYOK 代理）：配置管理、令牌稳定性、全量透传与审计日志。

覆盖：
- /proxy/set 创建 / 编辑（令牌保持稳定）/ 重置令牌 / 删除配置；
- 平台令牌鉴权：缺失 / 伪造 / 已禁用 → OpenAI 风格错误体；
- 全量透传：路径整体替换、认证替换、GET/POST、query 透传、SSE 流式；
- 上游 HTTP 错误与连接失败的原样 / 502 处理；
- 审计日志：请求体 + 响应体成对落库，鉴权失败也记录令牌快照；
- 上游 key 加密回环；管理端日志页鉴权。
"""

import io
from email.message import Message
from urllib.error import HTTPError, URLError

import pytest
from app import create_app, db
from app.config import Config
from app.models import ProxyConfig, ProxyLog
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


def _make_user(username="alice", role="user"):
    u = User(
        username=username,
        nickname=username,
        email=f"{username}@example.com",
        role=role,
    )
    u.set_password("pass123")
    db.session.add(u)
    db.session.commit()
    return u


def _login(client, username="alice"):
    client.post("/auth/login", data={"identifier": username, "password": "pass123"})


def _setup(client, base="https://api.example.com/v1", key="sk-real-key", enabled="1"):
    return client.post(
        "/proxy/set",
        data={
            "action": "save",
            "upstream_base_url": base,
            "upstream_api_key": key,
            "remark": "我的上游",
            "enabled": enabled,
        },
    )


class FakeResp:
    """模拟 urllib 上游响应。"""

    def __init__(self, status=200, headers=None, chunks=(b"",)):
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self._chunks = list(chunks)
        self.closed = False

    def read(self, n=8192):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def close(self):
        self.closed = True


# ---------- 配置管理 ----------

def test_set_config_creates_and_token_stable_on_edit(app, client):
    """首次创建签发令牌；编辑上游不改令牌、不清空密钥。"""
    with app.app_context():
        _make_user()
    _login(client)

    resp = _setup(client)
    assert resp.status_code == 302

    with app.app_context():
        cfg = ProxyConfig.query.one()
        token1 = cfg.token
        assert token1.startswith("sk-dnaisland-")
        assert cfg.user_id is not None
        assert cfg.upstream_base_url == "https://api.example.com/v1"

    # 编辑：只改 base URL，key 留空 → 令牌不变、密钥保留、base 更新
    resp = client.post(
        "/proxy/set",
        data={
            "action": "save",
            "upstream_base_url": "https://new.example.com/v1",
            "upstream_api_key": "",
            "remark": "改了",
            "enabled": "1",
        },
    )
    assert resp.status_code == 302
    with app.app_context():
        cfg = ProxyConfig.query.one()
        assert cfg.token == token1
        assert cfg.upstream_base_url == "https://new.example.com/v1"
        from app.services.proxy_service import decrypt_secret

        assert decrypt_secret(cfg.upstream_api_key) == "sk-real-key"


def test_set_config_requires_login(app, client):
    resp = client.get("/proxy/set")
    assert resp.status_code in (302, 401)


def test_reset_token_invalidates_old(app, client, monkeypatch):
    """重置令牌：新的生效、旧的 401。"""
    from app.routes import proxy as proxy_routes

    with app.app_context():
        _make_user()
    _login(client)
    _setup(client)

    with app.app_context():
        cfg = ProxyConfig.query.one()
        old_token = cfg.token

    # 旧令牌可用
    monkeypatch.setattr(
        proxy_routes, "open_upstream", lambda req: FakeResp(chunks=(b'{"ok":true}',))
    )
    r = client.get(
        "/proxyapi/v1/models", headers={"Authorization": f"Bearer {old_token}"}
    )
    assert r.status_code == 200

    # 重置
    r = client.post("/proxy/set", data={"action": "reset"})
    assert r.status_code == 302
    with app.app_context():
        cfg = ProxyConfig.query.one()
        new_token = cfg.token
    assert new_token != old_token

    # 旧令牌失效
    r = client.get(
        "/proxyapi/v1/models", headers={"Authorization": f"Bearer {old_token}"}
    )
    assert r.status_code == 401

    # 新令牌可用
    r = client.get(
        "/proxyapi/v1/models", headers={"Authorization": f"Bearer {new_token}"}
    )
    assert r.status_code == 200


def test_delete_config_keeps_logs(app, client, monkeypatch):
    from app.routes import proxy as proxy_routes

    with app.app_context():
        _make_user()
    _login(client)
    _setup(client)

    with app.app_context():
        cfg = ProxyConfig.query.one()
        token = cfg.token

    monkeypatch.setattr(
        proxy_routes, "open_upstream", lambda req: FakeResp(chunks=(b'{"ok":true}',))
    )
    client.get("/proxyapi/v1/models", headers={"Authorization": f"Bearer {token}"})

    r = client.post("/proxy/set", data={"action": "delete"})
    assert r.status_code == 302
    with app.app_context():
        assert ProxyConfig.query.count() == 0
        logs = ProxyLog.query.all()
        assert len(logs) == 1
        assert logs[0].config_id is None  # 日志保留，外键置空
        assert logs[0].user_id is not None


def test_two_users_independent_configs(app, client):
    with app.app_context():
        _make_user("alice")
        _make_user("bob")

    _login(client, "alice")
    _setup(client, base="https://a.example.com/v1", key="key-a")

    # 直接通过 service 层创建 bob 的配置，验证 user_id 唯一约束无冲突
    from app.services.proxy_service import upsert_config

    with app.app_context():
        bob_id = User.query.filter_by(username="bob").one().id
        cfg_b, err = upsert_config(
            bob_id,
            upstream_base_url="https://b.example.com/v1",
            upstream_api_key_plain="key-b",
        )
        assert err is None, f"bob create failed: {err}"
        assert cfg_b is not None

        cfgs = ProxyConfig.query.order_by(ProxyConfig.id).all()
        assert len(cfgs) == 2
        assert cfgs[0].token != cfgs[1].token
        assert cfgs[0].user_id != cfgs[1].user_id


# ---------- 鉴权 ----------

def test_relay_requires_valid_token(app, client):
    r = client.get("/proxyapi/v1/models")
    assert r.status_code == 401
    body = r.get_json()
    assert body["error"]["code"] == "invalid_api_key"

    r = client.get(
        "/proxyapi/v1/models", headers={"Authorization": "Bearer sk-not-ours"}
    )
    assert r.status_code == 401

    with app.app_context():
        assert ProxyLog.query.filter_by(user_id=None).count() == 2


def test_relay_disabled_config(app, client):
    with app.app_context():
        _make_user()
    _login(client)
    _setup(client, enabled="0")
    with app.app_context():
        token = ProxyConfig.query.one().token

    r = client.get(
        "/proxyapi/v1/models", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "proxy_disabled"


# ---------- 全量透传 ----------

def test_relay_forward_get(app, client, monkeypatch):
    from app.routes import proxy as proxy_routes

    with app.app_context():
        _make_user()
    _login(client)
    _setup(client)
    with app.app_context():
        token = ProxyConfig.query.one().token

    captured = {}

    def fake_open(req):
        captured["req"] = req
        return FakeResp(chunks=(b'{"ok":', b"true}"))

    monkeypatch.setattr(proxy_routes, "open_upstream", fake_open)

    r = client.get(
        "/proxyapi/v1/models?foo=bar",
        headers={"Authorization": f"Bearer {token}", "X-Custom": "abc"},
    )
    assert r.status_code == 200
    assert r.get_data() == b'{"ok":true}'
    assert r.content_type == "application/json"

    req = captured["req"]
    assert req.full_url == "https://api.example.com/v1/models?foo=bar"
    assert req.get_method() == "GET"
    assert req.get_header("Authorization") == "Bearer sk-real-key"
    assert req.get_header("X-custom") == "abc"  # urllib 会把头名首字母大写
    assert req.get_header("Cookie") is None  # 不透传 Cookie

    with app.app_context():
        log = ProxyLog.query.one()
        assert log.user_id is not None
        assert log.method == "GET"
        assert log.path == "/proxyapi/v1/models"
        assert log.upstream_url == "https://api.example.com/v1/models?foo=bar"
        assert log.status_code == 200
        assert log.response_body == '{"ok":true}'
        assert log.token == token
        assert log.duration_ms is not None


def test_relay_forward_post_sse(app, client, monkeypatch):
    from app.routes import proxy as proxy_routes

    with app.app_context():
        _make_user()
    _login(client)
    _setup(client)
    with app.app_context():
        token = ProxyConfig.query.one().token

    captured = {}
    sse_chunks = ['data: {"delta":"你好"}\n\n'.encode(), b"data: [DONE]\n\n"]

    def fake_open(req):
        captured["req"] = req
        return FakeResp(
            status=200,
            headers={"Content-Type": "text/event-stream"},
            chunks=sse_chunks,
        )

    monkeypatch.setattr(proxy_routes, "open_upstream", fake_open)

    payload = b'{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}'
    r = client.post(
        "/proxyapi/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 200
    assert r.get_data() == b"".join(sse_chunks)
    assert r.content_type == "text/event-stream"

    req = captured["req"]
    assert req.full_url == "https://api.example.com/v1/chat/completions"
    assert req.get_method() == "POST"
    assert req.data == payload
    assert req.get_header("Authorization") == "Bearer sk-real-key"

    with app.app_context():
        log = ProxyLog.query.one()
        assert log.status_code == 200
        assert log.request_body == payload.decode()
        assert log.response_body == b"".join(sse_chunks).decode()


def test_relay_upstream_http_error_passthrough(app, client, monkeypatch):
    from app.routes import proxy as proxy_routes

    with app.app_context():
        _make_user()
    _login(client)
    _setup(client)
    with app.app_context():
        token = ProxyConfig.query.one().token

    hdrs = Message()
    hdrs["Content-Type"] = "application/json"

    def fake_open(req):
        raise HTTPError(
            req.full_url, 429, "Too Many Requests", hdrs, io.BytesIO(b'{"error":"rate"}')
        )

    monkeypatch.setattr(proxy_routes, "open_upstream", fake_open)

    r = client.post(
        "/proxyapi/v1/chat/completions",
        data=b"{}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 429
    assert r.get_data() == b'{"error":"rate"}'
    assert r.content_type == "application/json"

    with app.app_context():
        log = ProxyLog.query.one()
        assert log.status_code == 429
        assert log.response_body == '{"error":"rate"}'
        assert "429" in log.error


def test_relay_upstream_connect_failure(app, client, monkeypatch):
    from app.routes import proxy as proxy_routes

    with app.app_context():
        _make_user()
    _login(client)
    _setup(client)
    with app.app_context():
        token = ProxyConfig.query.one().token

    def fake_open(req):
        raise URLError("connection refused")

    monkeypatch.setattr(proxy_routes, "open_upstream", fake_open)

    r = client.get(
        "/proxyapi/v1/models", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 502
    assert r.get_json()["error"]["code"] == "upstream_error"

    with app.app_context():
        log = ProxyLog.query.one()
        assert log.status_code == 502
        assert "connection refused" in log.error


# ---------- 加密 ----------

def test_encryption_roundtrip(app):
    from app.services.proxy_service import decrypt_secret, encrypt_secret

    with app.app_context():
        cipher = encrypt_secret("sk-super-secret")
        assert cipher != "sk-super-secret"
        assert decrypt_secret(cipher) == "sk-super-secret"
        assert decrypt_secret("garbage") == ""


# ---------- 管理端 ----------

def test_admin_logs_require_super_admin(app, client, monkeypatch):
    from app.routes import proxy as proxy_routes

    with app.app_context():
        _make_user("alice", role="user")
        _make_user("boss", role="super_admin")
        u = User.query.filter_by(username="alice").one()
        db.session.add(
            ProxyLog(
                user_id=u.id,
                token="sk-dnaisland-xyz",
                method="GET",
                path="/proxyapi/v1/models",
                request_body="",
                status_code=200,
                response_body='{"ok":true}',
            )
        )
        db.session.commit()

    # 普通用户不可见
    _login(client, "alice")
    assert client.get("/admin/proxy-logs").status_code == 403

    # 退出 alice，登录 boss（同一 client；logout 是 GET 路由）
    client.get("/auth/logout")
    resp = client.post(
        "/auth/login", data={"identifier": "boss", "password": "pass123"}
    )
    assert resp.status_code == 302, "boss login should redirect"
    r = client.get("/admin/proxy-logs")
    assert r.status_code == 200, f"boss admin page returned {r.status_code}"
    assert "/proxyapi/v1/models" in r.get_data(as_text=True)

    r = client.get("/admin/proxy-logs/1")
    assert r.status_code == 200
    # 响应体经 Jinja 转义后渲染（&quot; 为 " 的转义）
    assert "&#34;ok&#34;" in r.get_data(as_text=True)
