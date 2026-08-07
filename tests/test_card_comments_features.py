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
    """回复应作为楼中楼挂在父评论下，不重复出现在顶层列表。"""
    _seed(app)
    res = client.get("/api/card/c1/comments")
    assert res.status_code == 200
    data = res.get_json()
    # 顶层只含父评论（子回复不再平铺进 items，避免重复显示）
    assert len(data["items"]) == 1
    assert data["items"][0]["content"] == "作者评论"
    top = data["items"][0]
    assert len(top["replies"]) == 1
    assert top["replies"][0]["content"] == "回复内容"
    assert top["is_author"] is True
    assert "moderated" in top and "is_mine" in top
    # 子回复不占楼层、不置顶
    assert "floor" not in top["replies"][0] or top["replies"][0].get("floor") is None


def test_comments_nested_reply_thread(client, app):
    """楼中楼最多两层：子回复的子回复不再展开，且不进入顶层列表。

    库里即使存在第三层数据（历史数据），序列化也只输出两层。
    """
    with app.app_context():
        author = User(username="n_author", nickname="NAuthor", email="na@example.com")
        author.set_password("pw")
        u1 = User(username="n_u1", nickname="NU1", email="nu1@example.com")
        u1.set_password("pw")
        u2 = User(username="n_u2", nickname="NU2", email="nu2@example.com")
        u2.set_password("pw")
        db.session.add_all([author, u1, u2])
        db.session.commit()
        card = Card(id="card-nested", author_id=author.id, name="CardN", persona="P")
        db.session.add(card)
        db.session.commit()
        now = datetime.now(UTC)
        top = Comment(
            card_id="card-nested", user_id=author.id, content="顶层评论",
            created_at=now - timedelta(minutes=30),
        )
        db.session.add(top)
        db.session.flush()
        reply = Comment(
            card_id="card-nested", user_id=u1.id, content="直接回复",
            reply_to_id=top.id, created_at=now - timedelta(minutes=20),
        )
        db.session.add(reply)
        db.session.flush()
        deep = Comment(
            card_id="card-nested", user_id=u2.id, content="回复的回复",
            reply_to_id=reply.id, created_at=now - timedelta(minutes=10),
        )
        db.session.add(deep)
        db.session.commit()

    # Web 端接口：只输出两层，第三层数据不再展开。
    data = client.get("/api/card/card-nested/comments").get_json()
    assert len(data["items"]) == 1
    top_item = data["items"][0]
    assert top_item["content"] == "顶层评论"
    assert len(top_item["replies"]) == 1
    r1 = top_item["replies"][0]
    assert r1["content"] == "直接回复"
    assert r1["replies"] == []  # 最多两层：回复不再嵌套

    # App 端接口结构一致
    body = client.get("/api/v1/cards/card-nested/comments").get_json()
    assert body["ok"] is True
    items = body["data"]["items"]
    assert len(items) == 1
    assert items[0]["replies"][0]["replies"] == []


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


def test_app_api_comments_features(client, app):
    """App 端 /api/v1 评论接口同步楼中楼、仅看作者与楼层规则。"""
    _seed(app)
    res = client.get("/api/v1/cards/c1/comments")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    items = body["data"]["items"]
    # 顶层只含父评论，子回复作为楼中楼（不重复出现）
    assert len(items) == 1
    assert items[0]["content"] == "作者评论"
    top = items[0]
    assert len(top["replies"]) == 1
    assert top["replies"][0]["content"] == "回复内容"
    # 新增字段与楼层
    assert top["is_author"] is True
    assert "moderated" in top and "is_mine" in top
    assert top["floor"] is not None
    # 楼中楼子回复不带楼层
    assert "floor" not in top["replies"][0] or top["replies"][0].get("floor") is None
    # 仅看作者
    res2 = client.get("/api/v1/cards/c1/comments?only_author=1")
    d2 = res2.get_json()["data"]
    assert len(d2["items"]) == 1
    assert d2["items"][0]["content"] == "作者评论"
    assert d2["items"][0]["floor"] is None
    # 最热排序隐藏楼层
    res3 = client.get("/api/v1/cards/c1/comments?sort=hottest")
    d3 = res3.get_json()["data"]
    assert all(i["floor"] is None for i in d3["items"])
