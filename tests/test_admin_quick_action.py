"""后台「快捷调整积分」(quick-action adjust_points) 修复验证。

背景：adjust_points 构造 PointTransaction 时用了错误的字段
（amount/type/description），而模型实际字段为 delta/reason/source，
导致执行即抛 TypeError。本测试覆盖修复后的正确行为。
"""

import pytest
from app import create_app, db
from app.config import Config
from app.models.points import PointTransaction
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


def _login(client, identifier, password="pass123"):
    return client.post(
        "/auth/login", data={"identifier": identifier, "password": password}
    )


def test_adjust_points_adds_and_subtracts(app, client):
    """正数加积分、负数扣积分，且正确写入 PointTransaction 明细与通知。"""
    with app.app_context():
        admin = User(username="boss", nickname="老板", email="boss@x.com", role="super_admin")
        admin.set_password("pass123")
        target = User(username="t_user", nickname="目标", email="t@x.com")
        target.set_password("pass123")
        target.points = 100
        db.session.add_all([admin, target])
        db.session.commit()
        target_id = target.id

    assert _login(client, "boss").status_code == 302

    # 1) 加 50
    r = client.post(
        f"/admin/users/{target_id}/quick-action",
        json={"action": "adjust_points", "amount": "50", "reason": "奖励"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["points"] == 150

    with app.app_context():
        u = db.session.get(User, target_id)
        assert u.points == 150
        tx = (
            PointTransaction.query.filter_by(user_id=target_id)
            .order_by(PointTransaction.id.desc())
            .first()
        )
        assert tx.delta == 50
        assert tx.balance_after == 150
        assert tx.reason == "奖励"
        assert tx.source == "admin"

    # 2) 扣 60 -> 90（负数）
    r = client.post(
        f"/admin/users/{target_id}/quick-action",
        json={"action": "adjust_points", "amount": "-60", "reason": "回收"},
    )
    assert r.status_code == 200
    assert r.get_json()["points"] == 90

    with app.app_context():
        u = db.session.get(User, target_id)
        assert u.points == 90
        tx = (
            PointTransaction.query.filter_by(user_id=target_id)
            .order_by(PointTransaction.id.desc())
            .first()
        )
        assert tx.delta == -60
        assert tx.balance_after == 90
        assert tx.reason == "回收"
        # 通知已写入
        from app.models.notification import Notification

        assert Notification.query.filter_by(user_id=target_id).count() >= 2


def test_adjust_points_never_goes_negative(app, client):
    """余额不足扣成负数时被钳制为 0。"""
    with app.app_context():
        admin = User(username="boss2", nickname="老板2", email="boss2@x.com", role="super_admin")
        admin.set_password("pass123")
        target = User(username="poor", nickname="穷人", email="poor@x.com")
        target.set_password("pass123")
        target.points = 5
        db.session.add_all([admin, target])
        db.session.commit()
        target_id = target.id

    assert _login(client, "boss2").status_code == 302
    r = client.post(
        f"/admin/users/{target_id}/quick-action",
        json={"action": "adjust_points", "amount": "-100", "reason": "清零"},
    )
    assert r.status_code == 200
    assert r.get_json()["points"] == 0

    with app.app_context():
        u = db.session.get(User, target_id)
        assert u.points == 0
        tx = (
            PointTransaction.query.filter_by(user_id=target_id)
            .order_by(PointTransaction.id.desc())
            .first()
        )
        assert tx.delta == -100
        assert tx.balance_after == 0


def test_adjust_points_invalid_amount(app, client):
    """非数字与 0 被拒绝，不写任何明细。"""
    with app.app_context():
        admin = User(username="boss3", nickname="老板3", email="boss3@x.com", role="super_admin")
        admin.set_password("pass123")
        target = User(username="t3", nickname="目标3", email="t3@x.com")
        target.set_password("pass123")
        target.points = 10
        db.session.add_all([admin, target])
        db.session.commit()
        target_id = target.id

    assert _login(client, "boss3").status_code == 302

    for bad in ("abc", "0", ""):
        r = client.post(
            f"/admin/users/{target_id}/quick-action",
            json={"action": "adjust_points", "amount": bad, "reason": "x"},
        )
        assert r.status_code == 400
        assert r.get_json()["ok"] is False

    with app.app_context():
        u = db.session.get(User, target_id)
        assert u.points == 10  # 未被修改
        assert PointTransaction.query.filter_by(user_id=target_id).count() == 0


def test_adjust_points_requires_super_admin(app, client):
    """非超管访问被拒绝(403)。"""
    with app.app_context():
        plain = User(username="plain_u", nickname="普通", email="plain@x.com")
        plain.set_password("pass123")
        target = User(username="tgt", nickname="目标", email="tgt@x.com")
        target.set_password("pass123")
        db.session.add_all([plain, target])
        db.session.commit()
        target_id = target.id

    assert _login(client, "plain_u").status_code == 302
    r = client.post(
        f"/admin/users/{target_id}/quick-action",
        json={"action": "adjust_points", "amount": "10"},
    )
    assert r.status_code == 403
