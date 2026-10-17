"""世界卡的「发现」类接口：标签云、作者主页世界卡、详情词条索引。

三件事都是为了让世界卡不再是一个孤岛：

* /api/v1/worlds/tags —— 列表页要有筛选条，客户端不该自己猜标签；
* /api/v1/users/<username>/worlds —— 作者主页要有「世界」tab（网页版一直有）；
* 详情里的 entry_index —— App 要和网页版一样「按人物/名词分组 + 搜索词条」。

可见性一律走 WorldCard.visible_to，不另写一套规则。
"""

import json

import pytest
from app import create_app, db
from app.config import Config
from app.models.punishment import Punishment
from app.models.user import User
from app.models.world import WorldCard, WorldTag
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


def _payload(name="幻想大陆"):
    return json.dumps(
        {
            "name": name,
            "summary": "一个世界",
            "tags": ["奇幻"],
            "entries": [
                {"id": "e1", "name": "王都", "description": "首都", "type": "noun",
                 "keys": ["都城", "首都"]},
                {"id": "e2", "name": "艾琳", "description": "女王", "type": "person"},
                {"id": "e3", "name": "", "description": "没有名字的词条"},
            ],
        },
        ensure_ascii=False,
    )


def _seed(app):
    with app.app_context():
        author = User(username="wa", nickname="作者", email="wa@example.com")
        author.set_password("pass123")
        other = User(username="wb", nickname="路人", email="wb@example.com")
        other.set_password("pass123")
        banned = User(username="wc", nickname="被封", email="wc@example.com")
        banned.set_password("pass123")
        db.session.add_all([author, other, banned])
        db.session.commit()
        # 主页被封是处罚记录派生出来的（User.is_profile_banned），不是列。
        db.session.add(
            Punishment(user_id=banned.id, type="profile_banned", status="active")
        )
        db.session.commit()

        raw = _payload()
        rows = [
            ("w-approved", author.id, "approved", False, "奇幻"),
            ("w-pending", author.id, "pending", False, "未过审标签"),
            ("w-hidden", author.id, "approved", True, "已下架标签"),
            ("w-banned", banned.id, "approved", False, "被封作者标签"),
        ]
        for wid, author_id, status, hidden, tag in rows:
            db.session.add(
                WorldCard(
                    id=wid,
                    author_id=author_id,
                    name="幻想大陆",
                    summary="一个世界",
                    entry_count=3,
                    payload_bytes=len(raw.encode("utf-8")),
                    payload=raw,
                    status=status,
                    is_hidden=hidden,
                )
            )
            db.session.add(WorldTag(world_id=wid, tag=tag))
        db.session.commit()


def _login(client, username="wa"):
    client.post(
        "/auth/login", data={"identifier": username, "password": "pass123"}
    )


# ---------------------------------------------------------------------------
# 标签云
# ---------------------------------------------------------------------------


def test_tags_only_visible_worlds(app, client):
    """未过审 / 已下架的世界卡，标签不该出现在筛选条里。

    「主页被封」是另一层规则：visible_to 只过滤 hide_cards 处罚（与角色卡
    完全同一口径），主页层面的封禁由 /users/<username> 与 is_public 负责。
    所以这里不断言被封作者的标签。
    """
    _seed(app)
    body = client.get("/api/v1/worlds/tags").get_json()
    assert body["ok"] is True
    tags = [item["tag"] for item in body["data"]["tags"]]
    assert "奇幻" in tags
    assert "未过审标签" not in tags
    assert "已下架标签" not in tags


def test_tags_include_own_pending_for_author(app, client):
    """作者自己看列表时要能筛出自己待审核的条目（与列表可见性同一口径）。"""
    _seed(app)
    _login(client, "wa")
    tags = [it["tag"] for it in client.get("/api/v1/worlds/tags").get_json()["data"]["tags"]]
    assert "未过审标签" in tags
    # 已下架的仍然看不到（visible_to 对本人也会过滤 is_hidden）。
    assert "已下架标签" not in tags


# ---------------------------------------------------------------------------
# 作者主页的世界卡
# ---------------------------------------------------------------------------


def test_user_worlds_hides_pending_from_others(app, client):
    _seed(app)
    body = client.get("/api/v1/users/wa/worlds").get_json()
    ids = [it["id"] for it in body["data"]["items"]]
    assert ids == ["w-approved"]


def test_user_worlds_shows_own_pending(app, client):
    _seed(app)
    _login(client, "wa")
    ids = [it["id"] for it in client.get("/api/v1/users/wa/worlds").get_json()["data"]["items"]]
    assert "w-pending" in ids


def test_user_worlds_empty_for_banned_profile(app, client):
    """主页被封：返回空列表而不是 404，客户端据此显示空态而不是报错。"""
    _seed(app)
    body = client.get("/api/v1/users/wc/worlds").get_json()
    assert body["ok"] is True
    assert body["data"]["items"] == []
    assert body["data"]["total"] == 0


def test_user_worlds_unknown_user_is_404(app, client):
    _seed(app)
    assert client.get("/api/v1/users/nobody/worlds").status_code == 404


def test_user_worlds_items_carry_status_and_updated_at(app, client):
    """列表项要带 status / is_hidden —— 「我的世界卡」靠它画状态徽章。"""
    _seed(app)
    item = client.get("/api/v1/users/wa/worlds").get_json()["data"]["items"][0]
    assert item["status"] == "approved"
    assert item["is_hidden"] is False
    assert "updated_at" in item


# ---------------------------------------------------------------------------
# 详情词条索引
# ---------------------------------------------------------------------------


def test_detail_entry_index_has_type_and_keys(app, client):
    _seed(app)
    data = client.get("/api/v1/worlds/w-approved").get_json()["data"]
    index = data["entry_index"]
    names = [e["name"] for e in index]
    # 空名字的词条不进索引（网页版同口径）。
    assert names == ["王都", "艾琳"]
    assert index[0]["type"] == "noun"
    assert index[0]["keys"] == ["都城", "首都"]
    assert index[1]["type"] == "person"


def test_detail_entry_index_has_no_description(app, client):
    """正文（description）不进索引 —— 只给名字/类型/触发词，取舍与网页版一致。"""
    _seed(app)
    data = client.get("/api/v1/worlds/w-approved").get_json()["data"]
    for entry in data["entry_index"]:
        assert "description" not in entry
    # 示例词条仍然带正文（那一条是刻意的「试读」）。
    assert data["sample_entry"]["description"] == "首都"
