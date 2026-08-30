"""积分支持小数验证。

- 模型层：User.points / PointTransaction 读写小数（Decimal）。
- 后端 API：管理员调整积分支持小数，余额/明细用 Decimal 正确读写。
"""

import pytest
from app import create_app, db
from app.config import Config
from app.models.points import PointTransaction
from app.models.user import User
from decimal import Decimal
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


def test_points_columns_accept_decimals(app):
    """Numeric 列可写入并读回小数，且累加/明细一致。"""
    with app.app_context():
        u = User(username="dec_u", nickname="小数", email="dec@x.com")
        u.set_password("pw")
        u.points = Decimal("0.5")
        db.session.add(u)
        db.session.commit()

        got = db.session.get(User, u.id)
        assert got.points == Decimal("0.5")

        # 累加 0.25
        got.points += Decimal("0.25")
        db.session.add(
            PointTransaction(
                user_id=u.id, delta=Decimal("0.25"),
                balance_after=got.points, reason="t", source="consume",
            )
        )
        db.session.commit()
        got2 = db.session.get(User, u.id)
        assert got2.points == Decimal("0.75")
        tx = PointTransaction.query.first()
        assert tx.delta == Decimal("0.25")
        assert tx.balance_after == Decimal("0.75")


def test_admin_adjust_points_decimals(app, client):
    """后台快捷调整积分支持小数。"""
    with app.app_context():
        admin = User(username="boss_dec", nickname="老板", email="bossd@x.com", role="super_admin")
        admin.set_password("pw")
        target = User(username="t_dec", nickname="目标", email="td@x.com")
        target.set_password("pw")
        target.points = Decimal("10.00")
        db.session.add_all([admin, target])
        db.session.commit()
        target_id = target.id

    client.post("/auth/login", data={"identifier": "boss_dec", "password": "pw"})
    r = client.post(
        f"/admin/users/{target_id}/quick-action",
        json={"action": "adjust_points", "amount": "0.5", "reason": "小数加"},
    )
    assert r.status_code == 200
    assert r.get_json()["points"] == 10.5

    with app.app_context():
        u = db.session.get(User, target_id)
        assert u.points == Decimal("10.5")
        tx = PointTransaction.query.filter_by(user_id=target_id).first()
        assert tx.delta == Decimal("0.5")
        assert tx.balance_after == Decimal("10.5")

    # 扣 0.25
    r = client.post(
        f"/admin/users/{target_id}/quick-action",
        json={"action": "adjust_points", "amount": "-0.25", "reason": "小数扣"},
    )
    assert r.status_code == 200
    assert r.get_json()["points"] == 10.25

    with app.app_context():
        u = db.session.get(User, target_id)
        assert u.points == Decimal("10.25")


def test_admin_adjust_points_rejects_bad_decimal(app, client):
    """非法小数输入被拒绝，余额不变。"""
    with app.app_context():
        admin = User(username="boss_bad", nickname="老板", email="bossb@x.com", role="super_admin")
        admin.set_password("pw")
        target = User(username="t_bad", nickname="目标", email="tb@x.com")
        target.set_password("pw")
        target.points = Decimal("5")
        db.session.add_all([admin, target])
        db.session.commit()
        target_id = target.id

    client.post("/auth/login", data={"identifier": "boss_bad", "password": "pw"})
    for bad in ("abc", "1.2.3", ""):
        r = client.post(
            f"/admin/users/{target_id}/quick-action",
            json={"action": "adjust_points", "amount": bad, "reason": "x"},
        )
        assert r.status_code == 400

    with app.app_context():
        assert db.session.get(User, target_id).points == Decimal("5")


def test_api_points_serializes_decimal(app, client):
    """App API /api/v1/points 能把 Decimal 余额/明细序列化为 JSON（float）。"""
    with app.app_context():
        u = User(username="api_dec", nickname="API", email="api@x.com")
        u.set_password("pw")
        u.points = Decimal("0.5")
        db.session.add(u)
        db.session.commit()
        uid = u.id
        db.session.add(
            PointTransaction(
                user_id=uid, delta=Decimal("0.5"), balance_after=Decimal("0.5"),
                reason="充值", source="redeem",
            )
        )
        db.session.commit()

    # App API 用 session 登录（api_login_required 兼容 flask_login 会话）
    client.post("/auth/login", data={"identifier": "api_dec", "password": "pw"})
    r = client.get("/api/v1/points")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    data = body["data"]
    assert data["balance"] == 0.5
    assert isinstance(data["balance"], float)
    tx = data["items"][0]
    assert tx["delta"] == 0.5
    assert tx["balance_after"] == 0.5

