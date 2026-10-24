"""角色卡图片比例校验的后台开关。

开关存在 site_config.image_aspect_check_enabled（默认开）。
关掉后 create/update 不再强制 1:1 / 16:9 / 9:16。
"""

import base64
from io import BytesIO

import pytest
from app import create_app, db
from app.config import Config
from app.models.user import User
from app.services.card_publish_service import create_card_from_payload
from app.services.site_service import get_site_config
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
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.metadata.drop_all(bind=db.engine, checkfirst=True)


def _data_url(w, h):
    img = Image.new("RGB", (w, h), (10, 20, 30))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _author(name):
    u = User(username=name, nickname=name, email=f"{name}@x.com")
    u.set_password("pw")
    db.session.add(u)
    db.session.commit()
    return u


def test_default_is_enabled(app):
    with app.app_context():
        assert get_site_config().image_aspect_check_enabled is True


def test_switch_off_allows_wrong_ratio(app):
    with app.app_context():
        author = _author("s1")
        cfg = get_site_config()
        cfg.image_aspect_check_enabled = False
        db.session.commit()

        card, error = create_card_from_payload(
            author,
            {"name": "比例不对也放行", "images": {"square": _data_url(300, 100)}},
        )
        assert error is None, f"关掉开关后不该再拦，实际：{error!r}"
        assert card is not None


def test_switch_on_still_rejects_wrong_ratio(app):
    with app.app_context():
        author = _author("s2")
        cfg = get_site_config()
        cfg.image_aspect_check_enabled = True
        db.session.commit()

        card, error = create_card_from_payload(
            author,
            {"name": "比例不对", "images": {"square": _data_url(300, 100)}},
        )
        assert card is None
        assert error and "1:1" in error
