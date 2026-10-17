"""通知的「通用关联」扩展。

背景：通知原先只能关联角色卡（related_card_id）。世界卡要做审核/被复制通知，
需要一种不绑死角色卡的关联方式，于是加了 related_type + related_id。

两条硬约束：
* **related_card_id 不能删** —— 已发布的老客户端仍在读它做跳转；
* 老数据只有 related_card_id，接口必须能给出等价的 related_type/related_id。
"""

import pytest
from app import create_app, db
from app.config import Config
from app.models.notification import Notification
from app.routes.api import _notification_item
from app.services.notification_service import notify
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


def test_legacy_card_call_sets_both(app):
    """旧写法（只传 related_card_id）也要把新字段补齐。"""
    with app.app_context():
        n = notify(1, "卡片通过审核", type_="review", related_card_id="card-1")
        db.session.commit()
        assert n.related_card_id == "card-1"
        assert n.related_type == "card"
        assert n.related_id == "card-1"


def test_world_call_sets_generic_only(app):
    """新写法：世界卡不写 related_card_id，避免把 world id 塞进角色卡字段。"""
    with app.app_context():
        n = notify(
            1, "世界卡通过审核", type_="review", related_type="world", related_id="w-1"
        )
        db.session.commit()
        assert n.related_card_id is None
        assert n.related_type == "world"
        assert n.related_id == "w-1"


def test_serializer_backfills_legacy_rows(app):
    """老数据（迁移前写入、只有 related_card_id）序列化后也要有通用字段。"""
    with app.app_context():
        n = Notification(
            user_id=1, message="老通知", type="review", related_card_id="card-9"
        )
        db.session.add(n)
        db.session.commit()
        data = _notification_item(n)
        assert data["related_card_id"] == "card-9"
        assert data["related_type"] == "card"
        assert data["related_id"] == "card-9"


def test_serializer_world(app):
    with app.app_context():
        n = notify(1, "世界卡", type_="review", related_type="world", related_id="w-2")
        db.session.commit()
        data = _notification_item(n)
        assert data["related_card_id"] is None
        assert data["related_type"] == "world"
        assert data["related_id"] == "w-2"


def test_serializer_no_relation(app):
    with app.app_context():
        n = notify(1, "系统通知")
        db.session.commit()
        data = _notification_item(n)
        assert data["related_type"] is None
        assert data["related_id"] is None
