"""网页版角色卡评论区新增特性：楼中楼回复 / 仅看作者筛选 / 楼层规则。

对应 card_detail.html + /api/card/<card_id>/comments 接口的新增行为。
"""
from datetime import UTC, datetime, timedelta

import pytest
from app import create_app, db
from app.config import Config
from app.models.card import Card, Comment
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
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _seed(app, card_id="c1"):
    """作者评论 1 条 + 回复者评论 1 条（回复指向作者评论）。"""
    with app.app_context():
        author = User(username="author", nickname="Author", email="a@example.com")
        author.set_password("pw")
        commenter = User(
            username="commenter", nickname="Commenter", email="c@example.com"
        )
        commenter.set_password("pw")
        db.session.add_all([author, commenter])
        db.session.commit()
        card = Card(id=card_id, author_id=author.id, name="Card", persona="P")
        db.session.add(card)
        db.session.commit()
        now = datetime.now(UTC)
        top = Comment(
            card_id=card_id,
            user_id=author.id,
            content="作者评论",
            created_at=now - timedelta(minutes=10),
        )
        db.session.add(top)
        db.session.flush()
        reply = Comment(
            card_id=card_id,
            user_id=commenter.id,
            content="回复内容",
            reply_to_id=top.id,
            created_at=now - timedelta(minutes=5),
        )
        db.session.add(reply)
        db.session.commit()
        return author.id, top.id, reply.id


def test_comments_replies_thread(client, app):
    """回复应作为楼中楼挂在父评论下，并带 moderated/is_mine 字段。"""
    _seed(app)
    res = client.get("/api/card/c1/comments")
    assert res.status_code == 200
    data = res.get_json()
    # 平铺仍返回 2 条（保持向后兼容）
    assert len(data["items"]) == 2
    top = next(i for i in data["items"] if i["content"] == "作者评论")
    assert len(top["replies"]) == 1
    assert top["replies"][0]["content"] == "回复内容"
    assert top["replies"][0]["id"] == data["items"][0]["id"]
    assert top["is_author"] is True
    assert "moderated" in top and "is_mine" in top
    # 子回复不占楼层、不置顶
    assert "floor" not in top["replies"][0] or top["replies"][0].get("floor") is None


def test_comments_only_author_filter(client, app):
    """only_author=1 只返回作者评论，且楼层隐藏。"""
    _seed(app)
    res = client.get("/api/card/c1/comments?only_author=1")
    data = res.get_json()
    assert len(data["items"]) == 1
    assert data["items"][0]["content"] == "作者评论"
    assert data["items"][0]["floor"] is None


def test_comments_floor_rule(client, app):
    """楼层号只在最新排序下返回，最热排序返回 None（前端不展示）。"""
    _seed(app)
    # 最新：楼层存在
    data = client.get("/api/card/c1/comments?sort=latest").get_json()
    assert all(i["floor"] is not None for i in data["items"])
    # 最热：楼层隐藏
    data2 = client.get("/api/card/c1/comments?sort=hottest").get_json()
    assert all(i["floor"] is None for i in data2["items"])
