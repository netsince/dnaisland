from datetime import UTC, datetime, timedelta, timezone

import pytest
from app import create_app, db
from app.config import Config
from app.models.card import Card, Comment, CommentLike
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


def test_comment_like_model(app):
    """测试 CommentLike 点赞模型的添加与查询"""
    with app.app_context():
        user = User(username="liker", nickname="Liker", email="liker@example.com")
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()

        card = Card(id="card-like-1", author_id=user.id, name="Test Card", persona="Persona")
        db.session.add(card)
        db.session.commit()

        comment = Comment(card_id=card.id, user_id=user.id, content="Great card!")
        db.session.add(comment)
        db.session.commit()

        # 添加点赞
        like = CommentLike(user_id=user.id, comment_id=comment.id)
        db.session.add(like)
        db.session.commit()

        # 查询点赞
        fetched_like = CommentLike.query.filter_by(user_id=user.id, comment_id=comment.id).first()
        assert fetched_like is not None
        assert fetched_like.created_at is not None

        # 统计某个评论的点赞数
        likes_count = CommentLike.query.filter_by(comment_id=comment.id).count()
        assert likes_count == 1


def test_comment_is_pinned_attribute(app):
    """测试 Comment 的 is_pinned 属性"""
    with app.app_context():
        user = User(username="user_pin", nickname="PinUser", email="pin@example.com")
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()

        card = Card(id="card-pin-1", author_id=user.id, name="Pin Card", persona="Persona")
        db.session.add(card)
        db.session.commit()

        comment = Comment(card_id=card.id, user_id=user.id, content="Normal comment")
        db.session.add(comment)
        db.session.commit()

        assert comment.is_pinned is False

        # 修改为置顶
        comment.is_pinned = True
        db.session.commit()

        fetched_comment = db.session.get(Comment, comment.id)
        assert fetched_comment.is_pinned is True


def test_comment_reply_to_relationship(app):
    """测试 Comment 的 reply_to_id / reply_to 自关联关系"""
    with app.app_context():
        user1 = User(username="user_reply1", nickname="User1", email="u1@example.com")
        user1.set_password("password123")
        user2 = User(username="user_reply2", nickname="User2", email="u2@example.com")
        user2.set_password("password123")
        db.session.add_all([user1, user2])
        db.session.commit()

        card = Card(id="card-reply-1", author_id=user1.id, name="Reply Card", persona="Persona")
        db.session.add(card)
        db.session.commit()

        # 父评论
        parent_comment = Comment(card_id=card.id, user_id=user1.id, content="Parent comment")
        db.session.add(parent_comment)
        db.session.commit()

        # 子评论（回复父评论）
        child_comment = Comment(
            card_id=card.id,
            user_id=user2.id,
            content="Child reply comment",
            reply_to_id=parent_comment.id
        )
        db.session.add(child_comment)
        db.session.commit()

        # 校验 reply_to 关系
        fetched_child = db.session.get(Comment, child_comment.id)
        assert fetched_child.reply_to_id == parent_comment.id
        assert fetched_child.reply_to is not None
        assert fetched_child.reply_to.id == parent_comment.id
        assert fetched_child.reply_to.content == "Parent comment"

        # 校验 backref replies 关系
        fetched_parent = db.session.get(Comment, parent_comment.id)
        assert len(fetched_parent.replies) == 1
        assert fetched_parent.replies[0].id == child_comment.id
        assert fetched_parent.replies[0].content == "Child reply comment"


def test_card_comment_like_api(client, app):
    """测试点赞/取消点赞 API 及返回的数量与 liked 字段"""
    card_id = "card-like-api-1"
    with app.app_context():
        user = User(username="liker_api", nickname="LikerApi", email="liker_api@example.com")
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        user_id = user.id

        card = Card(id=card_id, author_id=user_id, name="Like Card", persona="Persona")
        db.session.add(card)
        db.session.commit()

        comment = Comment(card_id=card_id, user_id=user_id, content="Like me!")
        db.session.add(comment)
        db.session.commit()
        comment_id = comment.id

    # 1. 点赞
    client.post("/auth/login", data={"identifier": "liker_api", "password": "password123"})
    res = client.post(f"/card/{card_id}/comment/{comment_id}/like")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["liked"] is True
    assert data["count"] == 1

    # 校验 API 返回的 like_count 和 liked 字段
    res_comments = client.get(f"/api/card/{card_id}/comments")
    assert res_comments.status_code == 200
    c_data = res_comments.get_json()
    assert c_data["items"][0]["like_count"] == 1
    assert c_data["items"][0]["liked"] is True

    # 2. 取消点赞
    res = client.post(f"/card/{card_id}/comment/{comment_id}/like")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["liked"] is False
    assert data["count"] == 0

    # 校验 API 返回
    res_comments = client.get(f"/api/card/{card_id}/comments")
    c_data = res_comments.get_json()
    assert c_data["items"][0]["like_count"] == 0
    assert c_data["items"][0]["liked"] is False


def test_card_comment_pin_api(client, app):
    """测试置顶/取消置顶 API 及越权 403 拦截"""
    card_id = "card-pin-api-1"
    with app.app_context():
        author = User(username="author_pin", nickname="AuthorPin", email="author_pin@example.com")
        author.set_password("password123")
        normal_user = User(username="normal_user", nickname="NormalUser", email="normal@example.com")
        normal_user.set_password("password123")
        admin = User(username="admin_user", nickname="AdminUser", email="admin@example.com", role="super_admin")
        admin.set_password("password123")
        db.session.add_all([author, normal_user, admin])
        db.session.commit()

        card = Card(id=card_id, author_id=author.id, name="Pin Card API", persona="Persona")
        db.session.add(card)
        db.session.commit()

        comment = Comment(card_id=card_id, user_id=author.id, content="Comment to pin")
        db.session.add(comment)
        db.session.commit()
        comment_id = comment.id

    # 1. 普通用户越权置顶 -> 403
    client.post("/auth/login", data={"identifier": "normal_user", "password": "password123"})
    res = client.post(f"/card/{card_id}/comment/{comment_id}/pin")
    assert res.status_code == 403

    # 2. 卡片作者置顶 -> 200
    client.get("/auth/logout")
    client.post("/auth/login", data={"identifier": "author_pin", "password": "password123"})
    res = client.post(f"/card/{card_id}/comment/{comment_id}/pin")
    assert res.status_code == 200
    assert res.get_json()["ok"] is True
    assert res.get_json()["is_pinned"] is True

    # 校验 API 中 is_pinned 和 can_pin
    res_comments = client.get(f"/api/card/{card_id}/comments")
    c_data = res_comments.get_json()
    assert c_data["items"][0]["is_pinned"] is True
    assert c_data["items"][0]["can_pin"] is True

    # 再次请求取消置顶 -> 200
    res = client.post(f"/card/{card_id}/comment/{comment_id}/pin")
    assert res.status_code == 200
    assert res.get_json()["is_pinned"] is False

    # 3. 超级管理员置顶 -> 200
    client.get("/auth/logout")
    client.post("/auth/login", data={"identifier": "admin_user", "password": "password123"})
    res = client.post(f"/card/{card_id}/comment/{comment_id}/pin")
    assert res.status_code == 200
    assert res.get_json()["is_pinned"] is True


def test_card_comments_api_latest_hottest_and_pin_priority(client, app):
    """测试 card_comments_api 的 latest / hottest 排序以及 is_pinned 优先置顶规则"""
    card_id = "card-sort-1"
    with app.app_context():
        author = User(username="sort_author", nickname="SortAuthor", email="sort_author@example.com")
        author.set_password("password123")
        user1 = User(username="sort_user1", nickname="SortUser1", email="sort1@example.com")
        user1.set_password("password123")
        user2 = User(username="sort_user2", nickname="SortUser2", email="sort2@example.com")
        user2.set_password("password123")
        db.session.add_all([author, user1, user2])
        db.session.commit()

        card = Card(id=card_id, author_id=author.id, name="Sort Card", persona="Persona")
        db.session.add(card)
        db.session.commit()

        now = datetime.now(UTC)
        # cm1: 最早发布，0 赞，未置顶
        cm1 = Comment(card_id=card_id, user_id=author.id, content="cm1 earliest", created_at=now - timedelta(minutes=10))
        # cm2: 中间发布，有 2 个点赞，未置顶
        cm2 = Comment(card_id=card_id, user_id=user1.id, content="cm2 hottest", created_at=now - timedelta(minutes=5))
        # cm3: 最新发布，0 赞，已置顶
        cm3 = Comment(card_id=card_id, user_id=user2.id, content="cm3 pinned", created_at=now - timedelta(minutes=1), is_pinned=True)
        db.session.add_all([cm1, cm2, cm3])
        db.session.commit()
        cm3_id = cm3.id

        # 为 cm2 添加 2 个点赞
        db.session.add_all([
            CommentLike(user_id=author.id, comment_id=cm2.id),
            CommentLike(user_id=user1.id, comment_id=cm2.id),
        ])
        db.session.commit()

    # 1. 最新排序 (latest)：置顶 cm3 处于最前，然后最新的是 cm2, cm1
    res = client.get(f"/api/card/{card_id}/comments?sort=latest")
    items = res.get_json()["items"]
    assert len(items) == 3
    assert items[0]["content"] == "cm3 pinned"
    assert items[1]["content"] == "cm2 hottest"
    assert items[2]["content"] == "cm1 earliest"

    # 2. 最热排序 (hottest)：置顶 cm3 处于最前，然后点赞最多的是 cm2 (2赞)，然后 cm1 (0赞)
    res = client.get(f"/api/card/{card_id}/comments?sort=hottest")
    items = res.get_json()["items"]
    assert items[0]["content"] == "cm3 pinned"
    assert items[1]["content"] == "cm2 hottest"
    assert items[2]["content"] == "cm1 earliest"

    # 3. 取消 cm3 置顶后测试 hottest 排序
    with app.app_context():
        comment_3 = db.session.get(Comment, cm3_id)
        comment_3.is_pinned = False
        db.session.commit()

    res = client.get(f"/api/card/{card_id}/comments?sort=hottest")
    items = res.get_json()["items"]
    # cm2 (2 赞) 应该排第一
    assert items[0]["content"] == "cm2 hottest"
    assert items[0]["like_count"] == 2
    # cm3 (最新，0 赞) 应该排第二
    assert items[1]["content"] == "cm3 pinned"
    # cm1 (较旧，0 赞) 应该排第三
    assert items[2]["content"] == "cm1 earliest"


def test_card_comment_post_with_reply_to_id(client, app):
    """测试包含 reply_to_id 发送评论的关联"""
    card_id = "card-reply-post-1"
    with app.app_context():
        u1 = User(username="u1_reply", nickname="U1Reply", email="u1_reply@example.com")
        u1.set_password("password123")
        u2 = User(username="u2_reply", nickname="U2Reply", email="u2_reply@example.com")
        u2.set_password("password123")
        db.session.add_all([u1, u2])
        db.session.commit()

        card = Card(id=card_id, author_id=u1.id, name="Reply Card API", persona="Persona")
        db.session.add(card)
        db.session.commit()

        parent_cm = Comment(card_id=card_id, user_id=u1.id, content="Parent comment text")
        db.session.add(parent_cm)
        db.session.commit()
        parent_id = parent_cm.id

    client.post("/auth/login", data={"identifier": "u2_reply", "password": "password123"})
    res = client.post(
        f"/card/{card_id}/comment",
        data={"content": "Reply to parent text", "reply_to_id": parent_id},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert res.status_code == 200
    assert res.get_json()["ok"] is True

    # 查询 API 验证 reply_to 结构：子回复应挂在父评论的楼中楼里，
    # 而不是平铺进顶层 items（避免重复显示）。
    res_api = client.get(f"/api/card/{card_id}/comments")
    data = res_api.get_json()
    items = data["items"]
    assert len(items) == 1
    assert items[0]["content"] == "Parent comment text"
    replies = items[0]["replies"]
    assert len(replies) == 1
    child_item = replies[0]
    assert child_item["content"] == "Reply to parent text"
    assert child_item["reply_to"] is not None
    assert child_item["reply_to"]["id"] == parent_id
    assert child_item["reply_to"]["display_name"] == "U1Reply"

