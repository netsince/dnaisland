"""网页版「找回密码」。

用户反映没有找回密码入口 —— 这里补上：填邮箱 → 发验证码 → 填验证码 + 新密码 → 重置。

四个安全边界（都有用例守着，改代码时别把它们弄丢）：
1. **不可枚举账号**：`/auth/reset-code` 对「已注册 / 未注册 / 已封禁 / 冷却中」返回同一句话，
   只有邮箱格式错误才明确报错（否则用户填错也毫无反馈）；
2. **验证码用途分离**：重置用 `purpose="reset"`，注册码不能用来重置密码；
3. **提交接口按 IP 限流**：6 位码 10 分钟有效，不限次数就能暴力猜；
4. **封禁账号不能靠找回密码恢复访问**：不发码、也不接受提交。
"""

import pytest
from app import create_app, db
from app.config import Config
from app.models import User, VerificationCode
from app.routes import auth as auth_mod
from app.routes.auth import RESET_SUBMIT_LIMIT
from app.services.verification_code_service import create_code
from app.utils import _RATE_LIMITS
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
        _RATE_LIMITS.clear()  # 进程内限流是全局的：不清会让用例互相污染
        yield app
        _RATE_LIMITS.clear()
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def sent(monkeypatch):
    """捕获邮件发送（不真发信）。auth 模块是按名字导入的，所以补丁打在那里。"""
    box = {"reset": [], "changed": []}
    monkeypatch.setattr(
        auth_mod, "send_password_reset_email", lambda to, code: box["reset"].append((to, code))
    )
    monkeypatch.setattr(
        auth_mod, "send_password_changed_email", lambda to: box["changed"].append(to)
    )
    return box


def _user(email="u@x.com", password="oldpass123", role="user", status="active"):
    u = User(username=email.split("@")[0], nickname="用户", email=email, role=role, status=status)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    return u


def _codes(email, purpose=None):
    q = VerificationCode.query.filter_by(email=email)
    if purpose:
        q = q.filter_by(purpose=purpose)
    return q.all()


# ---------------------------------------------------------------------------
# 入口与页面
# ---------------------------------------------------------------------------


def test_login_page_has_forgot_password_link(client):
    html = client.get("/auth/login").get_data(as_text=True)
    assert "/auth/forgot" in html, "登录页必须有「忘记密码」入口"


def test_forgot_page_renders(client):
    r = client.get("/auth/forgot")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for label in ("找回密码", "注册邮箱", "邮箱验证码", "新密码", "确认新密码"):
        assert label in html, f"页面缺少「{label}」"


def test_logged_in_user_is_redirected_away(app, client):
    with app.app_context():
        _user()
    assert (
        client.post(
            "/auth/login", data={"identifier": "u@x.com", "password": "oldpass123"}
        ).status_code
        == 302
    )
    assert client.get("/auth/forgot").status_code == 302


# ---------------------------------------------------------------------------
# 发送验证码：不泄露账号是否存在
# ---------------------------------------------------------------------------


def test_reset_code_sends_mail_for_registered_email(app, client, sent):
    with app.app_context():
        _user(email="known@x.com")
    r = client.post("/auth/reset-code", json={"email": "known@x.com"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True and "已发送" in body["message"]
    assert len(sent["reset"]) == 1, "注册过的邮箱必须真的发信"
    to, code = sent["reset"][0]
    assert to == "known@x.com" and len(code) == 6 and code.isdigit()
    with app.app_context():
        rows = _codes("known@x.com", "reset")
        assert len(rows) == 1 and rows[0].code == code, "码要落库且与邮件一致"


def test_reset_code_does_not_reveal_unknown_email(app, client, sent):
    r = client.post("/auth/reset-code", json={"email": "nobody@x.com"})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert sent["reset"] == [], "未注册邮箱不能发信"
    with app.app_context():
        assert _codes("nobody@x.com") == []


def test_reset_code_response_is_identical_for_known_and_unknown(app, client, sent):
    """两条响应必须逐字相同（否则就是账号枚举接口）。"""
    with app.app_context():
        _user(email="known2@x.com")
    a = client.post("/auth/reset-code", json={"email": "known2@x.com"}).get_json()
    b = client.post("/auth/reset-code", json={"email": "unknown2@x.com"}).get_json()
    assert a == b


def test_reset_code_skips_locked_accounts(app, client, sent):
    """被封禁/已注销的账号：不发码，但对外文案不变。"""
    with app.app_context():
        _user(email="banned@x.com", status="admin_del")
    r = client.post("/auth/reset-code", json={"email": "banned@x.com"})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    assert sent["reset"] == []
    with app.app_context():
        assert _codes("banned@x.com", "reset") == []


def test_reset_code_respects_resend_cooldown(app, client, sent):
    with app.app_context():
        _user(email="cd@x.com")
    client.post("/auth/reset-code", json={"email": "cd@x.com"})
    client.post("/auth/reset-code", json={"email": "cd@x.com"})
    assert len(sent["reset"]) == 1, "60 秒内不重复发信"
    with app.app_context():
        assert len(_codes("cd@x.com", "reset")) == 1


def test_reset_code_rejects_malformed_email(client):
    r = client.post("/auth/reset-code", json={"email": "not-an-email"})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


# ---------------------------------------------------------------------------
# 提交重置
# ---------------------------------------------------------------------------


def test_reset_password_success(app, client, sent):
    with app.app_context():
        _user(email="ok@x.com")
        code = create_code("ok@x.com", purpose="reset")

    r = client.post(
        "/auth/reset-password",
        data={
            "email": "ok@x.com",
            "code": code,
            "password": "newpass456",
            "confirm_password": "newpass456",
        },
    )
    assert r.status_code == 302 and r.headers["Location"].endswith("/auth/login")

    with app.app_context():
        u = User.query.filter_by(email="ok@x.com").one()
        assert u.check_password("newpass456"), "新密码必须生效"
        assert not u.check_password("oldpass123"), "旧密码必须失效"
        assert _codes("ok@x.com", "reset") == [], "验证码用完即销毁"
    assert sent["changed"] == ["ok@x.com"], "改密后要通知账号主人"


def test_reset_password_does_not_log_the_user_in(app, client, sent):
    with app.app_context():
        _user(email="nologin@x.com")
        code = create_code("nologin@x.com", purpose="reset")
    client.post(
        "/auth/reset-password",
        data={
            "email": "nologin@x.com",
            "code": code,
            "password": "newpass456",
            "confirm_password": "newpass456",
        },
    )
    # 仍处于未登录状态：受保护页面会把人送回登录页
    r = client.get("/auth/logout")
    assert r.status_code == 302 and "/auth/login" in r.headers["Location"]


def test_reset_code_is_single_use(app, client, sent):
    with app.app_context():
        _user(email="once@x.com")
        code = create_code("once@x.com", purpose="reset")
    data = {
        "email": "once@x.com",
        "code": code,
        "password": "newpass456",
        "confirm_password": "newpass456",
    }
    assert client.post("/auth/reset-password", data=data).status_code == 302
    # 再用同一个码：应被拒（页面回显错误），密码保持第一次的结果
    r2 = client.post("/auth/reset-password", data=data)
    assert r2.status_code == 200
    with app.app_context():
        assert User.query.filter_by(email="once@x.com").one().check_password("newpass456")


def test_reset_rejects_register_purpose_code(app, client, sent):
    """用途分离：注册验证码不能用来重置密码。"""
    with app.app_context():
        _user(email="sep@x.com")
        reg_code = create_code("sep@x.com", purpose="register")
    r = client.post(
        "/auth/reset-password",
        data={
            "email": "sep@x.com",
            "code": reg_code,
            "password": "newpass456",
            "confirm_password": "newpass456",
        },
    )
    assert r.status_code == 200, "应回显错误而不是改密成功"
    with app.app_context():
        assert User.query.filter_by(email="sep@x.com").one().check_password("oldpass123")


def test_reset_rejects_wrong_code(app, client, sent):
    with app.app_context():
        _user(email="wrong@x.com")
        create_code("wrong@x.com", purpose="reset")
    r = client.post(
        "/auth/reset-password",
        data={
            "email": "wrong@x.com",
            "code": "000000",
            "password": "newpass456",
            "confirm_password": "newpass456",
        },
    )
    assert r.status_code == 200
    with app.app_context():
        assert User.query.filter_by(email="wrong@x.com").one().check_password("oldpass123")


def test_reset_rejects_mismatch_and_short_password(app, client, sent):
    with app.app_context():
        _user(email="val@x.com")
        code = create_code("val@x.com", purpose="reset")
    for data, why in (
        (
            {
                "email": "val@x.com",
                "code": code,
                "password": "abc12345",
                "confirm_password": "abc99999",
            },
            "两次不一致",
        ),
        (
            {"email": "val@x.com", "code": code, "password": "12345", "confirm_password": "12345"},
            "太短",
        ),
    ):
        r = client.post("/auth/reset-password", data=data)
        assert r.status_code == 200, why
        with app.app_context():
            assert User.query.filter_by(email="val@x.com").one().check_password("oldpass123"), why


def test_reset_rejects_locked_account_even_with_code(app, client, sent):
    """即使库里存在码（例如账号是在发码之后被封的），封禁账号也不能重置。"""
    with app.app_context():
        u = _user(email="lock@x.com", status="admin_del")
        code = create_code("lock@x.com", purpose="reset")
        u_id = u.id
    r = client.post(
        "/auth/reset-password",
        data={
            "email": "lock@x.com",
            "code": code,
            "password": "newpass456",
            "confirm_password": "newpass456",
        },
    )
    assert r.status_code == 200
    with app.app_context():
        assert db.session.get(User, u_id).check_password("oldpass123"), "封禁账号密码不得被改"


def test_reset_submit_is_rate_limited(app, client, sent):
    """暴力猜码必须被 IP 限流挡住。

    断言的是**行为**而不是文案：把额度用错误码打满后，连**正确**的验证码也提交不了，
    密码保持不变。（flash 文案在页面里是 JSON 转义过的，不适合做字符串断言。）
    """
    with app.app_context():
        _user(email="brute@x.com")
        good = create_code("brute@x.com", purpose="reset")

    for i in range(RESET_SUBMIT_LIMIT):  # 用错误码把这一窗口的额度打满
        client.post(
            "/auth/reset-password",
            data={
                "email": "brute@x.com",
                "code": f"{i:06d}",
                "password": "newpass456",
                "confirm_password": "newpass456",
            },
        )

    r = client.post(
        "/auth/reset-password",
        data={
            "email": "brute@x.com",
            "code": good,
            "password": "newpass456",
            "confirm_password": "newpass456",
        },
    )
    assert r.status_code == 200, "被限流时应回显页面而不是改密跳转"
    with app.app_context():
        assert User.query.filter_by(email="brute@x.com").one().check_password("oldpass123"), (
            "限流生效时即使验证码正确也不得改密"
        )


def test_email_builders_produce_a_message(app, monkeypatch):
    """真实邮件构造函数本身要能跑通（把真正的发送线程换成捕获，避免测试里真发信）。"""
    from app.services import email as email_mod

    captured = []
    monkeypatch.setattr(email_mod, "_send_async", lambda msg: captured.append(msg))

    email_mod.send_password_reset_email("a@x.com", "123456")
    email_mod.send_password_changed_email("a@x.com")

    assert len(captured) == 2
    assert "找回密码" in captured[0].subject
    assert "123456" in captured[0].body and "123456" in (captured[0].html or "")
    assert captured[0].recipients == ["a@x.com"]
    assert "密码已修改" in captured[1].subject
    assert captured[1].recipients == ["a@x.com"]


def test_new_password_can_log_in(app, client, sent):
    with app.app_context():
        _user(email="relogin@x.com")
        code = create_code("relogin@x.com", purpose="reset")
    client.post(
        "/auth/reset-password",
        data={
            "email": "relogin@x.com",
            "code": code,
            "password": "newpass456",
            "confirm_password": "newpass456",
        },
    )
    assert (
        client.post(
            "/auth/login", data={"identifier": "relogin@x.com", "password": "oldpass123"}
        ).status_code
        == 200
    ), "旧密码应登录失败（回显登录页）"
    assert (
        client.post(
            "/auth/login", data={"identifier": "relogin@x.com", "password": "newpass456"}
        ).status_code
        == 302
    ), "新密码应能登录"
