import io
import os

import pytest
from app import create_app, db
from app.config import Config
from app.models.card import Card, Comment
from app.models.notification import Notification
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


def test_comment_like_notification(app, client):
    """测试点赞他人评论生成通知，点赞自己评论不生成通知。"""
    with app.app_context():
        user1 = User(username="user1", nickname="用户一", email="u1@example.com")
        user1.set_password("pass123")
        user2 = User(username="user2", nickname="用户二", email="u2@example.com")
        user2.set_password("pass123")
        db.session.add_all([user1, user2])
        db.session.commit()
        user1_id = user1.id

        card = Card(id="card-1", author_id=user1.id, name="角色卡A", persona="Persona")
        db.session.add(card)
        db.session.commit()

        cm1 = Comment(card_id=card.id, user_id=user1.id, content="用户一的评论")
        db.session.add(cm1)
        db.session.commit()
        cm1_id = cm1.id

    # 1. user2 点赞 user1 的评论 -> 应给 user1 生成通知
    client.post("/auth/login", data={"identifier": "user2", "password": "pass123"})
    res = client.post(f"/card/card-1/comment/{cm1_id}/like")
    assert res.status_code == 200
    assert res.json["liked"] is True

    with app.app_context():
        n = Notification.query.filter_by(user_id=user1_id, type="comment_like").first()
        assert n is not None
        assert "用户二" in n.message
        assert "角色卡A" in n.message
        assert n.related_card_id == "card-1"

    # 2. user1 点赞自己 (user1) 的评论 -> 不应给 user1 生成新通知
    client.get("/auth/logout")
    client.post("/auth/login", data={"identifier": "user1", "password": "pass123"})

    with app.app_context():
        cm_self = Comment(card_id="card-1", user_id=user1_id, content="用户一自己的另一条评论")
        db.session.add(cm_self)
        db.session.commit()
        cm_self_id = cm_self.id
        count_before = Notification.query.filter_by(user_id=user1_id).count()

    res = client.post(f"/card/card-1/comment/{cm_self_id}/like")
    assert res.status_code == 200
    assert res.json["liked"] is True

    with app.app_context():
        count_after = Notification.query.filter_by(user_id=user1_id).count()
        assert count_after == count_before


def test_comment_reply_notification(app, client):
    """测试回复他人评论生成通知。"""
    with app.app_context():
        author = User(username="author", nickname="作者", email="author@example.com")
        author.set_password("pass123")
        u2 = User(username="user2", nickname="评论者A", email="u2@example.com")
        u2.set_password("pass123")
        u3 = User(username="user3", nickname="回复者B", email="u3@example.com")
        u3.set_password("pass123")
        db.session.add_all([author, u2, u3])
        db.session.commit()

        card = Card(id="card-2", author_id=author.id, name="角色卡B", persona="Persona")
        db.session.add(card)
        db.session.commit()

        # u2 发表主评论
        cm = Comment(card_id=card.id, user_id=u2.id, content="主评论")
        db.session.add(cm)
        db.session.commit()
        cm_id = cm.id
        u2_id = u2.id

    # u3 回复 u2 的评论 -> 应给 u2 生成 reply 通知
    client.post("/auth/login", data={"identifier": "user3", "password": "pass123"})
    res = client.post(
        "/card/card-2/comment",
        data={"content": "这是回复内容", "reply_to_id": cm_id},
    )
    assert res.status_code in (200, 302)

    with app.app_context():
        n = Notification.query.filter_by(user_id=u2_id, type="comment_reply").first()
        assert n is not None
        assert "回复者B" in n.message
        assert "角色卡B" in n.message
        assert "这是回复内容" in n.message
        assert n.related_card_id == "card-2"


def test_card_comment_author_notification(app, client):
    """测试在他人的卡片下发表评论通知卡片作者。"""
    with app.app_context():
        author = User(username="card_author", nickname="卡片作者", email="ca@example.com")
        author.set_password("pass123")
        visitor = User(username="visitor", nickname="访客", email="v@example.com")
        visitor.set_password("pass123")
        db.session.add_all([author, visitor])
        db.session.commit()

        card = Card(id="card-3", author_id=author.id, name="角色卡C", persona="Persona")
        db.session.add(card)
        db.session.commit()
        author_id = author.id

    # 1. 访客在作者的卡片下发评论 -> 应通知卡片作者
    client.post("/auth/login", data={"identifier": "visitor", "password": "pass123"})
    res = client.post(
        "/card/card-3/comment",
        data={"content": "很好看的卡片！"},
    )
    assert res.status_code in (200, 302)

    with app.app_context():
        n = Notification.query.filter_by(user_id=author_id, type="card_comment").first()
        assert n is not None
        assert "访客" in n.message
        assert "角色卡C" in n.message
        assert n.related_card_id == "card-3"

    # 2. 作者在自己的卡片下发评论 -> 不应产生卡片评论通知
    client.get("/auth/logout")
    client.post("/auth/login", data={"identifier": "card_author", "password": "pass123"})
    res = client.post(
        "/card/card-3/comment",
        data={"content": "谢谢支持！"},
    )
    assert res.status_code in (200, 302)

    with app.app_context():
        author_card_comments = Notification.query.filter_by(
            user_id=author_id, type="card_comment"
        ).all()
        # 仍然只有之前访客发评论产生的那 1 条
        assert len(author_card_comments) == 1


def test_comment_image_upload(app, client):
    """测试评论图片上传：合法图片保存与路径记录，非法后缀/超大文件校验，以及 API 返回 image_url。"""
    with app.app_context():
        user = User(username="img_user", nickname="图片测试员", email="img@example.com")
        user.set_password("pass123")
        db.session.add(user)
        db.session.commit()

        card = Card(id="card-img", author_id=user.id, name="带图角色卡", persona="Persona")
        db.session.add(card)
        db.session.commit()

    client.post("/auth/login", data={"identifier": "img_user", "password": "pass123"})

    # 1. 测试非法扩展名 (.txt / .exe)
    res_txt = client.post(
        "/card/card-img/comment",
        data={
            "content": "非法后缀评论",
            "image": (io.BytesIO(b"text content"), "test.txt"),
        },
        content_type="multipart/form-data",
    )
    assert res_txt.status_code == 400
    assert res_txt.json["error"] == "仅支持上传 png/jpg/jpeg/gif/webp 格式的图片"

    res_exe = client.post(
        "/card/card-img/comment",
        data={
            "content": "非法 exe 评论",
            "image": (io.BytesIO(b"exe content"), "test.exe"),
        },
        content_type="multipart/form-data",
    )
    assert res_exe.status_code == 400
    assert res_exe.json["error"] == "仅支持上传 png/jpg/jpeg/gif/webp 格式的图片"

    # 2. 测试超大文件 (> 5MB)
    big_data = b"0" * (5 * 1024 * 1024 + 1)
    res_big = client.post(
        "/card/card-img/comment",
        data={
            "content": "超大图片评论",
            "image": (io.BytesIO(big_data), "big.png"),
        },
        content_type="multipart/form-data",
    )
    assert res_big.status_code == 400
    assert res_big.json["error"] == "图片大小不能超过 5MB"

    # 3. 测试正常上传合法图片（生成一张真实可用的 PNG，避免 PIL 解码失败）
    from PIL import Image as _PILImage
    _buf = io.BytesIO()
    _PILImage.new("RGB", (4, 4), (200, 30, 30)).save(_buf, format="PNG")
    png_data = _buf.getvalue()
    res_ok = client.post(
        "/card/card-img/comment",
        data={
            "content": "合法带图评论",
            "image": (io.BytesIO(png_data), "sample.png"),
        },
        content_type="multipart/form-data",
    )
    assert res_ok.status_code in (200, 302)

    with app.app_context():
        cm = Comment.query.filter_by(card_id="card-img", content="合法带图评论").first()
        assert cm is not None
        # 现以 WebP base64 data URL 形式存于数据库，而非落地文件
        assert cm.image_data is not None
        assert cm.image_data.startswith("data:image/webp;base64,")

    # 4. 测试 card_comments_api 中精准包含 image_data
    res_api = client.get("/api/card/card-img/comments")
    assert res_api.status_code == 200
    api_data = res_api.json
    assert len(api_data["items"]) > 0
    item = api_data["items"][0]
    assert "image_data" in item
    assert item["image_data"] == cm.image_data


def test_comment_reply_flatten_to_top_level(app, client):
    """楼中楼最多两层：回复第二层时自动提升为对顶层评论的回复。"""
    with app.app_context():
        author = User(username="f_author", nickname="作者", email="fa@example.com")
        author.set_password("pass123")
        u1 = User(username="f_u1", nickname="用户一", email="fu1@example.com")
        u1.set_password("pass123")
        u2 = User(username="f_u2", nickname="用户二", email="fu2@example.com")
        u2.set_password("pass123")
        db.session.add_all([author, u1, u2])
        db.session.commit()

        card = Card(id="card-flat", author_id=author.id, name="两层卡", persona="P")
        db.session.add(card)
        db.session.commit()

        top = Comment(card_id=card.id, user_id=u1.id, content="顶层评论")
        db.session.add(top)
        db.session.flush()
        top_id = top.id
        reply = Comment(
            card_id=card.id, user_id=u2.id, content="第二层回复",
            reply_to_id=top_id,
        )
        db.session.add(reply)
        db.session.flush()
        reply_id = reply.id
        db.session.commit()

    # u1 回复「第二层回复」→ 应提升为对顶层评论的回复，不产生第三层。
    client.post("/auth/login", data={"identifier": "f_u1", "password": "pass123"})
    res = client.post(
        "/card/card-flat/comment",
        data={"content": "回复第二层", "reply_to_id": reply_id},
    )
    assert res.status_code in (200, 302)

    with app.app_context():
        new_cm = Comment.query.filter_by(card_id="card-flat", content="回复第二层").first()
        assert new_cm is not None
        assert new_cm.reply_to_id == top_id  # 提升到顶层
        assert new_cm.reply_to_id != reply_id

    # 序列化仍只有两层：顶层 replies 含两条第二层，且不再嵌套。
    data = client.get("/api/card/card-flat/comments").get_json()
    assert len(data["items"]) == 1
    top_item = data["items"][0]
    assert len(top_item["replies"]) == 2
    for r in top_item["replies"]:
        assert r["replies"] == []


def test_api_user_profile_comments(app, client):
    """GET /api/v1/users/<username>/comments 返回用户评论及所属卡片。"""
    with app.app_context():
        author = User(username="pc_author", nickname="作者", email="pc_a@example.com")
        author.set_password("pass123")
        u1 = User(username="pc_u1", nickname="评论者", email="pc_u1@example.com")
        u1.set_password("pass123")
        u2 = User(username="pc_u2", nickname="被回复", email="pc_u2@example.com")
        u2.set_password("pass123")
        db.session.add_all([author, u1, u2])
        db.session.commit()
        card = Card(id="card-comments-tab", author_id=author.id, name="评论卡", persona="P")
        db.session.add(card)
        db.session.commit()
        top = Comment(card_id=card.id, user_id=u1.id, content="我的评论")
        db.session.add(top)
        db.session.flush()
        top_id = top.id
        reply = Comment(
            card_id=card.id, user_id=u1.id, content="我的回复", reply_to_id=top_id,
        )
        db.session.add(reply)
        db.session.commit()

    body = client.get("/api/v1/users/pc_u1/comments").get_json()
    assert body["ok"] is True
    items = body["data"]["items"]
    # 两条评论都返回（同毫秒创建，顺序不保证）。
    assert {i["content"] for i in items} == {"我的回复", "我的评论"}
    reply_item = next(i for i in items if i["content"] == "我的回复")
    assert reply_item["author"]["username"] == "pc_u1"
    assert reply_item["reply_to"]["author_name"] == "评论者"  # 回复对象是顶层评论作者
    assert reply_item["card"]["id"] == "card-comments-tab"
    assert reply_item["card"]["name"] == "评论卡"

    # 用户不存在返回 404。
    assert client.get("/api/v1/users/nobody/comments").status_code == 404


