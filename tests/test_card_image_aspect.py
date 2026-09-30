"""角色卡图片槽位宽高比校验（服务端强制）。

背景：客户端裁剪此前可以自由改比例（App 甚至把槽位比例参数丢掉了），
服务端也完全不校验。本测试锁定服务端行为：新上传/替换的图必须符合槽位比例，
而存量未改动的图不受影响（否则老卡片会被一次编辑卡死）。
"""

import base64
from io import BytesIO

import pytest
from app import create_app, db
from app.config import Config
from app.models import CardImage
from app.models.user import User
from app.services.card_edit_service import update_card_from_payload
from app.services.card_publish_service import create_card_from_payload
from PIL import Image
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


def _png(w, h):
    img = Image.new("RGB", (w, h), (10, 20, 30))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _data_url(w, h):
    return "data:image/png;base64," + base64.b64encode(_png(w, h)).decode()


def _author(name):
    u = User(username=name, nickname=name, email=f"{name}@x.com")
    u.set_password("pw")
    db.session.add(u)
    db.session.commit()
    return u


# ---------------------------------------------------------------------------
# 新建：全量校验
# ---------------------------------------------------------------------------

def test_create_rejects_wrong_ratio_bytes(app):
    """Web 上传走原始字节。"""
    with app.app_context():
        author = _author("a1")
        card, error = create_card_from_payload(
            author, {"name": "x", "images": {"square": _png(300, 100)}}
        )
        assert card is None
        assert error and "1:1" in error


def test_create_rejects_wrong_ratio_data_url(app):
    """App API 走 base64 data URL，必须同样被校验（这条以前会漏掉）。"""
    with app.app_context():
        author = _author("a2")
        card, error = create_card_from_payload(
            author, {"name": "x", "images": {"landscape": _data_url(100, 100)}}
        )
        assert card is None
        assert error and "16:9" in error


@pytest.mark.parametrize(
    "slot,w,h",
    [("square", 200, 200), ("landscape", 160, 90), ("portrait", 90, 160)],
)
def test_create_accepts_correct_ratios(app, slot, w, h):
    with app.app_context():
        author = _author(f"ok_{slot}")
        card, error = create_card_from_payload(
            author, {"name": "x", "images": {slot: _png(w, h)}}
        )
        assert error is None
        assert card is not None


def test_create_tolerance_boundary(app):
    """±2% 容差：1% 偏差放行，10% 偏差拒绝。"""
    with app.app_context():
        author = _author("tol")
        card, error = create_card_from_payload(
            author, {"name": "x", "images": {"square": _png(100, 101)}}
        )
        assert error is None, f"1% 偏差应放行，实际: {error}"

        card2, error2 = create_card_from_payload(
            author, {"name": "y", "images": {"square": _png(100, 110)}}
        )
        assert card2 is None
        assert error2 and "1:1" in error2


def test_create_rejects_invalid_image(app):
    with app.app_context():
        author = _author("bad")
        card, error = create_card_from_payload(
            author, {"name": "x", "images": {"square": b"not-an-image"}}
        )
        assert card is None
        assert error


# ---------------------------------------------------------------------------
# 编辑：只校验被替换/新增的图，存量不动
# ---------------------------------------------------------------------------

def test_edit_skips_unchanged_legacy_image(app):
    """存量不合规图片原样回传时不得被卡住（不动存量数据）。"""
    with app.app_context():
        author = _author("e1")
        card, error = create_card_from_payload(
            author, {"name": "n", "images": {"square": _png(200, 200)}}
        )
        assert error is None
        db.session.commit()

        # 模拟历史数据：把存量图改成不合规比例
        img = CardImage.query.filter_by(card_id=card.id, slot="square").first()
        legacy = _data_url(300, 100)
        img.data = legacy
        db.session.commit()

        # 编辑时原样回传 -> 视为未改动 -> 放行
        assert update_card_from_payload(
            card, {"name": "n2", "images": {"square": legacy}}
        ) is None


def test_edit_rejects_replaced_wrong_ratio_image(app):
    with app.app_context():
        author = _author("e2")
        card, error = create_card_from_payload(
            author, {"name": "n", "images": {"square": _png(200, 200)}}
        )
        assert error is None
        db.session.commit()

        # 换成 3:1 的新图 -> 必须拒绝
        err = update_card_from_payload(
            card, {"name": "n2", "images": {"square": _data_url(300, 100)}}
        )
        assert err and "1:1" in err
        # 拒绝后不应改动已有图片
        assert CardImage.query.filter_by(card_id=card.id, slot="square").first() is not None


# ---------------------------------------------------------------------------
# 接口层
# ---------------------------------------------------------------------------

def test_api_publish_rejects_wrong_ratio(app, client):
    with app.app_context():
        _author("api1")
    client.post("/auth/login", data={"identifier": "api1", "password": "pw"})
    r = client.post(
        "/api/v1/cards/publish",
        json={"name": "x", "images": {"square": _data_url(300, 100)}},
    )
    assert r.status_code == 400
    assert "1:1" in r.get_json()["error"]
