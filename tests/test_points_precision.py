"""积分精度（DECIMAL(30,10)，10 位小数）与字符串化传输的契约测试。

覆盖：
- app.constants 的规范化格式化与输入校验（>10 位小数必须报错，不得静默四舍五入）
- 模型列的 precision/scale，以及 MySQL DDL 渲染（防止再出现"只有 scale 没有 precision"）
- JSON provider 把 Decimal 序列化为字符串
- API 返回字符串而非浮点数
- 网页模板过滤器能显示 10 位小数
"""

from decimal import Decimal

import pytest
import sqlalchemy as sa
from app import create_app, db
from app.config import Config
from app.constants import (
    POINT_MAX_ABS,
    POINT_PRECISION,
    POINT_SCALE,
    parse_points_input,
    points_add,
    points_mul,
    points_sub,
    points_to_signed_str,
    points_to_str,
)
from app.models.image_gen import GenerationLog, GenerationModel
from app.models.points import KeyUsageLog, PointTransaction, RedemptionKey
from app.models.user import User
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable


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


# ---------------------------------------------------------------------------
# 常量与格式化
# ---------------------------------------------------------------------------

def test_precision_constants():
    assert POINT_PRECISION == 30
    assert POINT_SCALE == 10
    assert Decimal("99999999999999999999.9999999999") == POINT_MAX_ABS


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "0"),
        ("", "0"),
        (0, "0"),
        (5, "5"),
        (Decimal("0"), "0"),
        (Decimal("0.0000000000"), "0"),
        (Decimal("-0.0000000000"), "0"),
        (Decimal("10.2500000000"), "10.25"),
        (Decimal("1E+2"), "100"),
        (Decimal("0.0000000001"), "0.0000000001"),
        (Decimal("-0.5000000000"), "-0.5"),
        (Decimal("12345678901234567890.1234567891"), "12345678901234567890.1234567891"),
        (0.5, "0.5"),
        ("10.2500000000", "10.25"),
    ],
)
def test_points_to_str_canonical(value, expected):
    assert points_to_str(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (Decimal("5"), "+5"),
        (Decimal("0"), "+0"),
        (Decimal("-0.5"), "-0.5"),
        (None, "+0"),
    ],
)
def test_points_to_signed_str(value, expected):
    assert points_to_signed_str(value) == expected


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0.1234567891", Decimal("0.1234567891")),
        ("0.0000000001", Decimal("1E-10")),
        ("1.230000000000000", Decimal("1.23")),  # 尾零不算有效小数位
        ("-5", Decimal("-5")),
        ("99999999999999999999.9999999999", POINT_MAX_ABS),
    ],
)
def test_parse_points_input_accepts(raw, expected):
    assert parse_points_input(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "0.00000000001",      # 11 位小数
        "1.23456789012",      # 11 位小数
        "1e20",               # 超出 DECIMAL(30,10) 容量
        "abc",
        "1.2.3",
        "",
        "   ",
        None,
        "NaN",
        "Infinity",
    ],
)
def test_parse_points_input_rejects(raw):
    with pytest.raises(ValueError):
        parse_points_input(raw)


def test_arithmetic_helpers_keep_full_precision():
    """默认 decimal 上下文只有 28 位精度；30 位有效数字的加减必须不被截断。"""
    high = Decimal("99999999999999999999.9999999999")
    assert points_sub(high, Decimal("0.0000000001")) == Decimal("99999999999999999999.9999999998")
    assert points_add(Decimal("0.1234567891"), Decimal("0.0000000001")) == Decimal("0.1234567892")
    assert points_mul(3, Decimal("0.1234567891")) == Decimal("0.3703703673")
    # 对照：默认上下文下同一个减法会被舍入
    assert high - Decimal("0.0000000001") != Decimal("99999999999999999999.9999999998")


def test_arithmetic_helpers_treat_none_as_zero():
    assert points_add(None, Decimal("0.5")) == Decimal("0.5")
    assert points_sub(None, Decimal("0.5")) == Decimal("-0.5")
    assert points_mul(None, 3) == Decimal("0")


# ---------------------------------------------------------------------------
# 模型列定义
# ---------------------------------------------------------------------------

def test_model_columns_use_point_precision(app):
    columns = [
        User.__table__.c.points,
        PointTransaction.__table__.c.delta,
        PointTransaction.__table__.c.balance_after,
        RedemptionKey.__table__.c.points,
        KeyUsageLog.__table__.c.points_gained,
        GenerationModel.__table__.c.points_per_image,
        GenerationLog.__table__.c.points_spent,
    ]
    for col in columns:
        assert isinstance(col.type, sa.Numeric), f"{col} 不是 Numeric"
        assert col.type.precision == POINT_PRECISION, f"{col} precision 不符"
        assert col.type.scale == POINT_SCALE, f"{col} scale 不符"


def test_mysql_ddl_renders_numeric_30_10(app):
    """MySQL/MariaDB DDL 必须显式带 precision，否则会退化成 NUMERIC（= scale 0）。"""
    ddl = str(CreateTable(User.__table__).compile(dialect=mysql.dialect()))
    assert "NUMERIC(30, 10)" in ddl


def test_sqlite_roundtrip_keeps_ten_decimals(app):
    with app.app_context():
        u = User(username="p10", nickname="精度", email="p10@x.com")
        u.set_password("pw")
        u.points = Decimal("0.1234567891")
        db.session.add(u)
        db.session.commit()
        assert db.session.get(User, u.id).points == Decimal("0.1234567891")


# ---------------------------------------------------------------------------
# JSON 序列化
# ---------------------------------------------------------------------------

def test_json_provider_serializes_decimal_as_string(app):
    assert app.json.default(Decimal("10.2500000000")) == "10.25"
    assert app.json.default(Decimal("0.0000000001")) == "0.0000000001"


def test_template_filter_shows_ten_places(app):
    f = app.jinja_env.filters["points"]
    assert f(Decimal("0.1234567891")) == "0.1234567891"
    assert f(Decimal("10.2500000000")) == "10.25"
    assert f(None) == "0"


def test_api_points_returns_strings(app, client):
    with app.app_context():
        u = User(username="api_str", nickname="API", email="apis@x.com")
        u.set_password("pw")
        u.points = Decimal("0.1234567891")
        db.session.add(u)
        db.session.commit()
        uid = u.id
        db.session.add(
            PointTransaction(
                user_id=uid, delta=Decimal("0.1234567891"),
                balance_after=Decimal("0.1234567891"), reason="充值", source="redeem",
            )
        )
        db.session.commit()

    client.post("/auth/login", data={"identifier": "api_str", "password": "pw"})
    r = client.get("/api/v1/points")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["balance"] == "0.1234567891"
    assert isinstance(data["balance"], str)
    tx = data["items"][0]
    assert tx["delta"] == "0.1234567891"
    assert tx["balance_after"] == "0.1234567891"


def test_web_points_page_shows_ten_decimals(app, client):
    """网页点数详情页必须显示 10 位小数（旧实现 "%f" 只显示 6 位、%+.2f 只显示 2 位）。"""
    with app.app_context():
        u = User(username="web10", nickname="网页", email="web10@x.com")
        u.set_password("pw")
        u.points = Decimal("0.1234567891")
        db.session.add(u)
        db.session.commit()
        db.session.add(
            PointTransaction(
                user_id=u.id, delta=Decimal("0.1234567891"),
                balance_after=Decimal("0.1234567891"), reason="充值", source="redeem",
            )
        )
        db.session.commit()

    client.post("/auth/login", data={"identifier": "web10", "password": "pw"})
    r = client.get("/points/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "0.1234567891" in html
    assert "+0.1234567891" in html


# ---------------------------------------------------------------------------
# 后台输入：>10 位小数必须拒绝，不得静默四舍五入
# ---------------------------------------------------------------------------

def _make_admin_and_target(app, admin_name, target_name, points):
    with app.app_context():
        admin = User(username=admin_name, nickname="老板", email=f"{admin_name}@x.com", role="super_admin")
        admin.set_password("pw")
        target = User(username=target_name, nickname="目标", email=f"{target_name}@x.com")
        target.set_password("pw")
        target.points = points
        db.session.add_all([admin, target])
        db.session.commit()
        return target.id


def test_admin_quick_action_accepts_ten_decimals(app, client):
    tid = _make_admin_and_target(app, "boss10", "t10", Decimal("1"))
    client.post("/auth/login", data={"identifier": "boss10", "password": "pw"})
    r = client.post(
        f"/admin/users/{tid}/quick-action",
        json={"action": "adjust_points", "amount": "0.1234567891", "reason": "十位"},
    )
    assert r.status_code == 200
    assert r.get_json()["points"] == "1.1234567891"
    with app.app_context():
        assert db.session.get(User, tid).points == Decimal("1.1234567891")


def test_admin_quick_action_rejects_eleven_decimals(app, client):
    tid = _make_admin_and_target(app, "boss11", "t11", Decimal("1"))
    client.post("/auth/login", data={"identifier": "boss11", "password": "pw"})
    r = client.post(
        f"/admin/users/{tid}/quick-action",
        json={"action": "adjust_points", "amount": "0.00000000001", "reason": "越界"},
    )
    assert r.status_code == 400
    with app.app_context():
        assert db.session.get(User, tid).points == Decimal("1")
